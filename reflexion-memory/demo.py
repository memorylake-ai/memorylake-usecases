#!/usr/bin/env python3
"""
Reflection memory for a self-improving (Reflexion-style) agent — a MemoryLake
demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Kestrel Labs (fictional) runs Tern, a CI triage agent. Between 2026-09-22 and
2026-10-03 Tern got five failures wrong: it quarantined a healthy test,
rolled back a good release, raised a timeout that hid a regression. After each
run Dana Okafor, the on-call engineer, said what had really happened, and Tern
wrote a reflection: the cause, the adjustment, the expected outcome.

A reflection that lives only in the next prompt is gone a run later. Here each
one is written to **Tern's own memory**, an agent scope (`fact add --agent`,
typed with `fact update --metadata`). The runs themselves are conversations in
the team's CI project, so the incidents land on the project and the lessons on
the agent.

- The fifth lesson contradicts the first ("retry ECONNRESET once" vs "never
  retry it"). MemoryLake's contradiction detector flags it on the agent
  (`fact conflict list --agent`), and `keep_fact` settles it.
- Today two new failures arrive. Tern's planning step searches its memory (the
  agent's `actor_id`) and plans around the lessons that match. A twin made
  with `agent fork` has the same prompt and config, but no memory, so it plans
  the same mistakes.
- Two live lessons share a cause, so they become a skill (`skill create`,
  security review `safe`). Tern moves to agent version 2 with the skill
  attached, and its memory is unchanged: memory is not configuration.
- The audit trail (`fact trace`, `fact conflict get`, `agent version list`) shows how
  Tern's behavior changed and why.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-rfx"  # memorylake-usecases / reflexion-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-ci",
    name="Kestrel Labs — CI incidents",
    description="The CI runs Tern triaged, as conversations with the on-call engineer. Demo data.",
)

# The detector checks a write right after it lands, if the scope has been quiet. Back-to-back writes
# are checked minutes later, as one batch. So every write waits until the previous one is GAP seconds old.
GAP = 15
DETECT_WAIT = 150    # upper bound for the detector to answer the fifth lesson (usually 10–40 s)
SETTLE = 30          # and nothing is called "not raised" until the last write is this old
COOK_WAIT = 240      # upper bound for the five runs' conversations to be processed
SKILL_WAIT = 90      # upper bound for a skill's security review (usually a few seconds)


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
    return json.loads((DATA / "agent.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def skill_name(story: dict) -> str:
    return cid(story["skill"]["slug"])


def reflection_meta(r: dict) -> dict:
    x = r["reflection"]
    return dict(type="reflection", run=r["run"], date=r["date"], kind=r["kind"], cause=x["cause"],
                adjustment=x["adjustment"], expected=x["expected"], signals=x["signals"])


def run_of(story: dict, run_id: str) -> dict:
    return next(r for r in story["runs"] if r["run"] == run_id)


# --------------------------------------------------------------------------- pacing

class Pace:
    """Keeps writes GAP seconds apart, so the detector checks each one as it lands."""

    def __init__(self) -> None:
        self.last = 0.0

    def wait(self) -> None:
        left = GAP - (time.time() - self.last)
        if left > 0:
            time.sleep(left)

    def wrote(self) -> None:
        self.last = time.time()


PACE = Pace()


# --------------------------------------------------------------------------- lookups

def _pages(cli: CLI, args: list[str], echo: bool = True, scoped: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, "--page-size", "100", *(["--continuation-token", token] if token else []),
                       scoped=scoped, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def find_project(cli: CLI, echo: bool = True):
    try:
        return cli.run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_actor(cli: CLI, slug: str, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(slug), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_agent(cli: CLI, slug: str, echo: bool = True):
    try:
        return cli.run("agent", "get", cid(slug), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_skill(cli: CLI, story: dict, echo: bool = True):
    name = skill_name(story)
    items = (cli.run("skill", "list", "--name", name, "--page-size", "100", echo=echo) or {}).get("items") or []
    return next((s for s in items if s.get("name") == name), None)


def find_conv(cli: CLI, run_id: str, echo: bool = True):
    try:
        return cli.run("conv", "get", cid(run_id), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", flag, scope_id], echo)


def list_conflicts(cli: CLI, agent_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "conflict", "list", "--agent", agent_id, *extra], echo)


def bind(cli: CLI, *args: str) -> None:
    try:
        cli.run(*args, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


# --------------------------------------------------------------------------- step 2: setup

def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created the CI project “{proj['name']}” → {proj['id']}")
    else:
        note(f"CI project already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def ensure_human(cli: CLI, story: dict) -> str:
    h = story["human"]
    actor = find_actor(cli, h["slug"])
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(h["slug"]), "--display-name", h["name"],
                        "--description", h["description"])
        note(f"created {h['name']} → {actor['id']}")
    bind(cli, "actor", "bind", "--actor", actor["id"])
    return actor["id"]


def ensure_agent(cli: CLI, story: dict) -> dict:
    a = story["agent"]
    agent = find_agent(cli, a["slug"])
    if agent is None:
        agent = cli.run("agent", "create", "--name", a["name"], "--custom-id", cid(a["slug"]),
                        "--description", a["description"], "--system-prompt", a["system_prompt"])
        note(f"created the agent {a['name']} → {agent['id']} (version {agent.get('version')}); "
             f"it comes with its own actor {agent['actor_id']}, which is how its memory is searched")
    else:
        note(f"agent already exists → {agent['id']} (version {agent.get('version')})")
    bind(cli, "agent", "bind", agent["id"])
    return agent


def ensure_twin(cli: CLI, story: dict, agent: dict) -> dict:
    t = story["twin"]
    twin = find_agent(cli, t["slug"])
    if twin is None:
        twin = cli.run("agent", "fork", agent["id"], "--custom-id", cid(t["slug"]), "--name", t["name"])
        note(f"forked {agent['id']} → twin {twin['id']}: same prompt and configuration, its own actor "
             f"{twin['actor_id']}, and no memory (a fork copies configuration, never memory)")
    else:
        note(f"twin already exists → {twin['id']}")
    bind(cli, "agent", "bind", twin["id"])
    return twin


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything this demo created, then starting over")
        cleanup(cli, quiet=True)
    project_id = ensure_project(cli)
    human_id = ensure_human(cli, story)
    agent = ensure_agent(cli, story)
    twin = ensure_twin(cli, story, agent)
    ids = dict(project=project_id, human=human_id, agent=agent["id"], agent_actor=agent["actor_id"],
               twin=twin["id"], twin_actor=twin["actor_id"])
    emit("text", f"\n  CI project   {project_id} (the runs, as conversations: incidents land here)"
                 f"\n  Dana         {human_id} (on-call engineer)"
                 f"\n  Tern         {agent['id']} · actor {agent['actor_id']} (its lessons land here)"
                 f"\n  Tern twin    {twin['id']} · actor {twin['actor_id']} (agent fork: same config, no memory)")
    emit("setup", "", ids=ids, agent=story["agent"], twin=story["twin"], human=story["human"], project=PROJECT["name"],
         version=agent.get("version"), twin_version=twin.get("version"))
    return ids


# --------------------------------------------------------------------------- step 3: runs + reflections

def transcript(story: dict, r: dict) -> list[tuple[str, str, str]]:
    dana, tern = story["human"]["name"], "Tern"
    return [("human", f"{r['date']}T02:00:00Z", f"{dana} (on-call engineer, Kestrel Labs): {r['task']} Can you triage it?"),
            ("agent", f"{r['date']}T02:05:00Z", f"{tern} (CI triage agent, Kestrel Labs): {r['action']}"),
            ("human", f"{r['date']}T06:00:00Z", f"{dana} (on-call engineer, Kestrel Labs): Follow-up on {r['run']}: {r['outcome']}")]


def ensure_runs(cli: CLI, ids: dict, story: dict) -> list[dict]:
    """One DIRECT conversation per run (Dana ↔ Tern's actor) in the CI project. Returns what was appended."""
    who = dict(human=ids["human"], agent=ids["agent_actor"])
    out = []
    for r in story["runs"]:
        conv = find_conv(cli, r["run"], echo=False)
        if conv is None:
            conv = cli.run("conv", "create", "--project", ids["project"], "--name", f"{r['run']} · {r['task'][:60]}",
                           "--kind", "DIRECT", "--actors", f"{ids['human']},{ids['agent_actor']}",
                           "--custom-id", cid(r["run"]), "--metadata", f"run={r['run']}", "--metadata", f"date={r['date']}",
                           scoped=True)
        msgs = _pages(cli, ["conv", "msg", "list", conv["id"]], echo=False, scoped=False)
        appended = 0
        for i, (role, ts, text) in enumerate(transcript(story, r)[len(msgs):], start=len(msgs)):
            cli.run("conv", "msg", "append", conv["id"], "--actor", who[role], "--custom-id", f"{r['run']}-m{i + 1}",
                    "--timestamp", ts, "--text", text, scoped=True, echo=appended == 0)
            appended += 1
        out.append(dict(run=r["run"], conv=conv["id"], appended=appended))
        emit("run", "", run=r["run"], date=r["date"], task_kind=r["kind"], task=r["task"], action=r["action"], outcome=r["outcome"],
             conv=conv["id"], appended=appended)
    return out


def wait_cooked(cli: CLI, convs: list[dict]) -> int:
    pending = {c["conv"]: c["run"] for c in convs if c["appended"]}
    if not pending:
        note("the five runs were recorded by an earlier run of the demo")
        return 0
    note(f"waiting for MemoryLake to process {len(pending)} conversation(s) (they cook in parallel)")
    t0, first = time.time(), True
    while pending and time.time() - t0 < COOK_WAIT:
        for conv_id in list(pending):
            try:
                st = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                st = {}
            first = False
            if st.get("cook_finished"):
                pending.pop(conv_id)
        if pending:
            time.sleep(5)
    waited = int(time.time() - t0)
    if pending:
        note(f"still processing after {waited}s: {', '.join(pending.values())} — the demo goes on; facts arrive later")
    else:
        note(f"processed in {waited}s")
    return waited


def forgotten_texts(cli: CLI, agent_id: str) -> set[str]:
    """Texts of lessons an earlier run's resolution forgot: they must not be pinned again on a re-run."""
    out = set()
    for c in list_conflicts(cli, agent_id, "--resolved", "true", echo=False):
        gone = set((c.get("resolve") or {}).get("forgotten_fact_ids") or [])
        if not gone:
            full = cli.run("fact", "conflict", "get", c["id"], "--agent", agent_id, scoped=True, echo=False) or {}
            gone = set((full.get("resolve") or {}).get("forgotten_fact_ids") or [])
        out |= {s.get("fact_text") for s in c.get("fact_snapshots") or [] if s.get("fact_id") in gone}
    return out


def pin_reflection(cli: CLI, agent_id: str, text: str, meta: dict) -> str:
    """The Reflexion "write" step: `fact add --agent` (verbatim) + `fact update --metadata` (typed). Paced."""
    PACE.wait()
    res = cli.run("fact", "add", "--agent", agent_id, text, scoped=True)
    PACE.wrote()
    fid = ((res or {}).get("facts") or [{}])[0].get("id")
    cli.run("fact", "update", fid, "--agent", agent_id, "--metadata", json.dumps(meta), scoped=True)
    return fid


def ensure_reflections(cli: CLI, ids: dict, story: dict) -> dict[str, dict]:
    live = {f.get("fact"): f for f in list_facts(cli, "--agents", ids["agent"])}
    skip = forgotten_texts(cli, ids["agent"])
    out = {}
    for r in story["runs"]:
        text, meta = r["reflection"]["text"], reflection_meta(r)
        f = live.get(text)
        if f is None and text in skip:
            st = dict(id=None, forgotten=True, new=False)
        elif f is None:
            st = dict(id=pin_reflection(cli, ids["agent"], text, meta), forgotten=False, new=True)
        else:
            if any((f.get("metadata") or {}).get(k) != v for k, v in meta.items()):
                cli.run("fact", "update", f["id"], "--agent", ids["agent"], "--metadata", json.dumps(meta), scoped=True)
            st = dict(id=f["id"], forgotten=False, new=False)
        out[r["run"]] = st
        emit("reflection", "", run=r["run"], id=st["id"], fact=text, forgotten=st["forgotten"], new=st["new"], **{
            k: meta[k] for k in ("cause", "adjustment", "expected", "signals", "date")}, task_kind=meta["kind"])
    return out


def runs_step(cli: CLI, ids: dict, story: dict) -> dict:
    note("each run is a DIRECT conversation between Dana and Tern's actor, in the CI project; "
         "Dana's follow-up says what really happened")
    convs = ensure_runs(cli, ids, story)
    waited = wait_cooked(cli, convs)
    note(f"after each run Tern writes a typed reflection into its own memory: `fact add --agent` + "
         f"`fact update --metadata` (cause, adjustment, expected outcome), {GAP}s apart")
    got = ensure_reflections(cli, ids, story)
    lines = ["\n  Five runs, five reflections:"]
    for r in story["runs"]:
        st = got[r["run"]]
        x = r["reflection"]
        lines.append(f"\n  {r['run']}  {r['date']}  {clip(r['task'], 86)}")
        lines.append(f"         did:      {clip(r['action'], 86)}")
        lines.append(f"         actually: {clip(r['outcome'], 86)}")
        lines.append(f"         lesson:   {clip(x['adjustment'], 86)}  [cause: {x['cause']}]")
        lines.append(f"                   {'forgotten by an earlier resolution — not pinned again' if st['forgotten'] else st['id']}")
    emit("text", "\n".join(lines))
    return dict(convs=convs, cook_seconds=waited, reflections=got)


# --------------------------------------------------------------------------- step 4: what Tern remembers

def reflection_view(f: dict) -> dict:
    m = f.get("metadata") or {}
    return dict(id=f.get("id"), text=f.get("fact"), run=m.get("run"), date=m.get("date"), kind=m.get("kind"),
                cause=m.get("cause"), adjustment=m.get("adjustment"), expected=m.get("expected"),
                signals=[s for s in (m.get("signals") or "").split("|") if s])


def read_memory(cli: CLI, ids: dict, echo: bool = True) -> dict:
    agent_facts = list_facts(cli, "--agents", ids["agent"], echo)
    reflections = sorted([reflection_view(f) for f in agent_facts if (f.get("metadata") or {}).get("type") == "reflection"],
                         key=lambda r: r["run"] or "")
    extracted = [dict(id=f.get("id"), text=f.get("fact")) for f in agent_facts if (f.get("metadata") or {}).get("type") != "reflection"]
    project = [dict(id=f.get("id"), text=f.get("fact")) for f in list_facts(cli, "--projects", ids["project"], echo)]
    twin = list_facts(cli, "--agents", ids["twin"], echo)
    return dict(reflections=reflections, extracted=extracted, project=project, twin=len(twin))


def remembers(cli: CLI, ids: dict, story: dict) -> dict:
    mem = read_memory(cli, ids)
    lines = [f"\n  Tern's memory (`fact list --agents {ids['agent']}`): {len(mem['reflections'])} typed reflection(s)"]
    for r in mem["reflections"]:
        lines.append(f"    {r['run']}  cause={r['cause']:<11} {clip(r['adjustment'], 88)}")
    if mem["extracted"]:
        lines.append(f"\n  Also on Tern, extracted from its own turns in the conversations ({len(mem['extracted'])}; "
                     "not relied on — extraction varies from run to run):")
        lines += [f"    · {clip(f['text'], 104)}" for f in mem["extracted"]]
    lines.append(f"\n  The CI project (`fact list --projects {ids['project']}`): {len(mem['project'])} fact(s) about the incidents")
    lines += [f"    · {clip(f['text'], 104)}" for f in mem["project"][:8]]
    if len(mem["project"]) > 8:
        lines.append(f"    … and {len(mem['project']) - 8} more")
    lines.append(f"\n  The twin (`fact list --agents {ids['twin']}`): {mem['twin']} fact(s)")
    emit("text", "\n".join(lines))
    emit("memory", "", **mem)
    return mem


# --------------------------------------------------------------------------- planning (steps 5–6, `plan`)

def relevant(r: dict, task: str) -> bool:
    t = task.lower()
    return any(s.lower() in t for s in r["signals"])


def guess_kind(task: str) -> str:
    t = task.lower()
    return "timeout" if ("timed out" in t or "timeout" in t) else "deploy-failure" if ("deploy" in t or "smoke" in t) else "test-failure"


def plan(cli: CLI, ids: dict, story: dict, task: str, kind: str | None = None, who: str = "agent",
         echo: bool = True, expect: str | None = None) -> dict:
    """The Reflexion planning step: retrieve the agent's lessons for this failure, then plan around them.

    `search` always returns its top k, related or not, so a hit counts only if one of its signals
    appears in the task; the rest are listed as left out. Two matching lessons that sit in an open
    conflict make the plan say "ask first" instead of picking one."""
    actor = ids["agent_actor"] if who == "agent" else ids["twin_actor"]
    scope = ids["agent"] if who == "agent" else ids["twin"]
    res = cli.run("search", task, "--actors", actor, "--types", "fact", "--top-k", "5", scoped=True, echo=echo) or {}
    by_id = {f["id"]: reflection_view(f) for f in list_facts(cli, "--agents", scope, echo=False)
             if (f.get("metadata") or {}).get("type") == "reflection"}
    hits, other = [], 0
    for h in res.get("facts") or []:
        r = by_id.get(h.get("id"))
        if r and relevant(r, task):
            hits.append(dict(r, rank=h.get("rank")))
        else:
            other += 1
    open_ = {}
    if who == "agent" and len(hits) > 1:
        for c in list_conflicts(cli, ids["agent"], "--resolved", "false", echo=False):
            for f in c.get("fact_ids") or []:
                open_[f] = c["id"]
    disputed = sorted({open_[h["id"]] for h in hits if h["id"] in open_})
    default = story["playbook"].get(kind or guess_kind(task), story["playbook"]["test-failure"])
    if not hits:
        steps, verdict = [default], "default"
    elif disputed:
        steps, verdict = [f"Two of my lessons disagree ({', '.join(disputed)}): ask the on-call engineer before acting."], "ask"
    else:
        steps, verdict = [h["adjustment"] for h in hits] + [f"Only if those checks are clean: {default[0].lower() + default[1:]}"], "lessons"
    top = hits[0]["run"] if hits else None
    out = dict(who=who, task=task, hits=hits, other=other, disputed=disputed, steps=steps, verdict=verdict,
               top=top, expect=expect, ok=(top == expect) if expect else None)
    return out


def show_plan(p: dict, label: str) -> None:
    lines = [f"\n  {label}"]
    if p["hits"]:
        for h in p["hits"]:
            lines.append(f"    #{h['rank']} {h['run']} ({h['date']}, cause {h['cause']}): {clip(h['adjustment'], 84)}")
    else:
        lines.append("    no lesson in memory matches this failure")
    if p["other"]:
        lines.append(f"    ({p['other']} other hit(s) left out: none of their signals is in the task)")
    lines.append("    plan:")
    lines += [f"      {i}. {clip(s, 100)}" for i, s in enumerate(p["steps"], 1)]
    if p["expect"]:
        lines.append(f"    {'✓' if p['ok'] else '✗'} top lesson {p['top'] or '—'} (expected {p['expect']})")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 5: detector + keep_fact

def conflict_view(c: dict, story: dict) -> dict:
    run_by_text = {r["reflection"]["text"]: r["run"] for r in story["runs"]}
    facts = [dict(id=s.get("fact_id"), text=s.get("fact_text"), run=run_by_text.get(s.get("fact_text")))
             for s in c.get("fact_snapshots") or []]
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
            lines.append(f"    {f['id']}  ({f['run'] + ' lesson' if f['run'] else 'extracted from a run conversation'})")
            lines.append(f"      “{clip(f['text'], 110)}”")
    emit("text", "\n".join(lines))


def detector(cli: CLI, ids: dict, story: dict, refl: dict[str, dict]) -> dict:
    late = next(r for r in story["runs"] if r["reflection"].get("contradicts"))
    early = run_of(story, late["reflection"]["contradicts"])
    late_id = refl[late["run"]]["id"]
    fresh = refl[late["run"]]["new"]

    def naming(cs):
        return [conflict_view(c, story) for c in cs if late_id in (c.get("fact_ids") or [])]

    t0 = time.time()
    if fresh:
        note(f"{late['run']}'s lesson contradicts {early['run']}'s; waiting for MemoryLake's contradiction detector on "
             f"Tern's memory (up to {DETECT_WAIT}s; nothing counts as “not raised” until the last write is {SETTLE}s old)")
        emit("detector_wait", "", seconds=DETECT_WAIT)
        first, found = True, []
        while time.time() - t0 < DETECT_WAIT:
            time.sleep(5)
            found = naming(list_conflicts(cli, ids["agent"], echo=first))
            first = False
            if found and time.time() - PACE.last >= SETTLE:
                break
    else:
        note("the lessons were written by an earlier run; reading what the detector raised then")
        found = naming(list_conflicts(cli, ids["agent"]))
    waited = int(time.time() - t0)
    others = [conflict_view(c, story) for c in list_conflicts(cli, ids["agent"], echo=False)
              if late_id not in (c.get("fact_ids") or [])]
    if found:
        show_conflicts(found, f"\n  `fact conflict list --agent {ids['agent']}` — {len(found)} conflict(s) naming {late['run']}'s lesson"
                              + (f" (after {waited}s)" if fresh else "") + ":")
    else:
        emit("text", f"\n  ✗ nothing raised on {late['run']}'s lesson" + (f" within {waited}s" if fresh else "")
                     + " — the detector sometimes answers minutes later, as a batch; check with `python3 demo.py audit`")
    if others:
        show_conflicts(others, f"\n  {len(others)} other conflict(s) on Tern's memory (not planned by the story — shown, not hidden):")
    task = next(t for t in story["tasks"] if t["expect"] == late["run"])
    before = plan(cli, ids, story, task["task"], task["kind"], expect=task["expect"])
    show_plan(before, f"Tern's plan for “{clip(task['task'], 70)}” right now"
                      + ("" if fresh else " (the conflict was settled by an earlier run)") + ":")
    out = dict(conflicts=found, others=others, waited=waited, fresh=fresh, late=late["run"], early=early["run"], before=before)
    emit("detector", "", **out)
    return out


def resolve(cli: CLI, ids: dict, story: dict, det: dict) -> dict:
    late = run_of(story, det["late"])
    done = []
    for c in [c for c in det["conflicts"] if not c["resolved"]]:
        keep = next((f for f in c["facts"] if f["run"] == late["run"]), None)
        if keep is None:
            continue
        why = f"{late['run']} ({late['date']}): {late['outcome']}"
        emit("text", f"\n  {c['id']}: keep {late['run']}'s lesson — the later run is the evidence: {clip(late['outcome'], 110)}")
        r = cli.run("fact", "conflict", "resolve", c["id"], "--agent", ids["agent"], "--strategy", "keep_fact",
                    "--keep-fact-id", keep["id"], scoped=True) or {}
        note(f"resolved with {r.get('strategy')}: forgotten {', '.join(r.get('forgotten_fact_ids') or []) or 'nothing'}")
        done.append(dict(id=c["id"], name=c["name"], strategy=r.get("strategy"), kept=keep["id"],
                         forgotten=r.get("forgotten_fact_ids") or [], why=why))
    if not done:
        note("nothing open to resolve (resolved by an earlier run, or the detector has not answered yet)")
    if done:
        time.sleep(2)
        task = next(t for t in story["tasks"] if t["expect"] == late["run"])
        after = plan(cli, ids, story, task["task"], task["kind"], expect=task["expect"])
        show_plan(after, "Tern's plan for the same failure, after the resolution:")
    else:
        after = det["before"]
    out = dict(resolved=done, after=after)
    emit("resolved", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: today's planning, Tern vs twin

def today(cli: CLI, ids: dict, story: dict) -> list[dict]:
    note(f"two failures came in overnight ({story['today']}). Both agents plan with the same retrieval step; "
         "only Tern has lessons to retrieve")
    out = []
    for t in story["tasks"]:
        emit("text", f"\n  ── {t['task']}")
        a = plan(cli, ids, story, t["task"], t["kind"], who="agent", expect=t["expect"])
        show_plan(a, "Tern (search --actors <Tern's actor>):")
        b = plan(cli, ids, story, t["task"], t["kind"], who="twin")
        show_plan(b, "Twin (search --actors <twin's actor>):")
        out.append(dict(key=t["key"], task=t["task"], expect=t["expect"], tern=a, twin=b))
    emit("today", "", tasks=out)
    return out


# --------------------------------------------------------------------------- step 7: reflections → skill → version 2

def skill_markdown(story: dict, lessons: list[dict]) -> str:
    sk = story["skill"]
    runs = ", ".join(l["run"] for l in lessons)
    body = [f"---", f"name: {skill_name(story)}",
            # Quoted: an unquoted colon makes the front matter invalid YAML, and the security review
            # then says `blocked` without a reason (measured: same text, quoted → safe).
            f"description: {json.dumps('Check the environment before acting on a CI failure. Learned by Tern from runs ' + runs + ' (cause: ' + sk['cause'] + ').')}",
            f"---", "", f"# {sk['title']}", "",
            f"Tern wrote these lessons into its MemoryLake memory after getting a CI failure wrong. "
            f"Each one blamed the code for what was really the {sk['cause']}.", "", "Before you act on a failure:", ""]
    body += [f"{i}. {l['adjustment']} _(run {l['run']}, {l['date']}; fact `{l['id']}`)_" for i, l in enumerate(lessons, 1)]
    body += ["", "Act on the code only when every check above is clean.", ""]
    return "\n".join(body)


def skill_zip(md: str, path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("SKILL.md", date_time=(2026, 10, 8, 0, 0, 0))
        z.writestr(info, md)
    path.write_bytes(buf.getvalue())


def wait_safe(cli: CLI, skill_id: str) -> dict:
    t0, first = time.time(), True
    while True:
        s = cli.run("skill", "get", skill_id, echo=first) or {}
        first = False
        if s.get("latest_security_status") != "pending" or time.time() - t0 > SKILL_WAIT:
            s["waited"] = int(time.time() - t0)
            return s
        time.sleep(3)


def skill_step(cli: CLI, ids: dict, story: dict) -> dict:
    sk = story["skill"]
    mem = read_memory(cli, ids, echo=False)
    lessons = [r for r in mem["reflections"] if r["cause"] == sk["cause"]]
    by_cause: dict[str, int] = {}
    for r in mem["reflections"]:
        by_cause[r["cause"]] = by_cause.get(r["cause"], 0) + 1
    emit("text", f"\n  Live reflections by cause: " + ", ".join(f"{k} ×{v}" for k, v in sorted(by_cause.items())))
    if len(lessons) < sk["min_reflections"]:
        emit("text", f"\n  Only {len(lessons)} live reflection(s) with cause “{sk['cause']}” (need {sk['min_reflections']}); no skill yet.")
        out = dict(skill=None, lessons=lessons)
        emit("skill", "", **out)
        return out
    note(f"{len(lessons)} live lessons share the cause “{sk['cause']}” ({', '.join(l['run'] for l in lessons)}): "
         "the pattern becomes a skill, written from those lessons")
    md = skill_markdown(story, lessons)
    OUT.mkdir(exist_ok=True)
    pkg = OUT / f"{skill_name(story)}.zip"
    (OUT / "SKILL.md").write_text(md, encoding="utf-8")
    skill_zip(md, pkg)
    emit("text", "\n  out/SKILL.md:\n" + "\n".join("    " + l for l in md.splitlines()))
    skill = find_skill(cli, story)
    if skill is None:
        skill = cli.run("skill", "create", "--name", skill_name(story), "--title", sk["title"],
                        "--description", f"Learned by Tern from runs {', '.join(l['run'] for l in lessons)}.",
                        "--package", os.path.relpath(pkg))
        note(f"published skill {skill['id']} v{skill.get('latest_version')} — security review {skill.get('latest_security_status')}")
    elif skill.get("latest_security_status") in ("blocked", "error", "errored"):
        v = cli.run("skill", "version", "create", skill["id"], "--package", os.path.relpath(pkg)) or {}
        note(f"v{skill.get('latest_version')} was {skill.get('latest_security_status')}; published v{v.get('version')} — "
             f"security review {v.get('security_status')}")
    else:
        note(f"skill already published by an earlier run → {skill['id']} v{skill.get('latest_version')}")
    skill = wait_safe(cli, skill["id"])
    status = skill.get("latest_security_status")
    note(f"security review: {status}" + (f" (after {skill['waited']}s)" if skill.get("waited") else ""))
    agent = cli.run("agent", "get", ids["agent"]) or {}
    count_before = len(list_facts(cli, "--agents", ids["agent"], echo=False))
    ref = dict(skill_id=skill["id"], skill_version=skill.get("latest_version"))
    created = None
    if any(s.get("skill_id") == skill["id"] for s in agent.get("skills") or []):
        note(f"Tern's version {agent.get('version')} already uses the skill")
    elif status != "safe":
        note(f"the skill is {status}, so it cannot be attached yet (agents refuse SKILL_NOT_USABLE); run the demo again later")
    else:
        cfg = OUT / "tern-next-version.json"
        cfg.write_text(json.dumps({"skills": (agent.get("skills") or []) + [ref]}, indent=2) + "\n", encoding="utf-8")
        created = cli.run("agent", "version", "create", ids["agent"], "--from-version", "latest", "--config", os.path.relpath(cfg))
        note(f"Tern is now version {created.get('version')}: same system prompt, plus the skill")
    versions = sorted((cli.run("agent", "version", "list", ids["agent"]) or {}).get("items") or [], key=lambda v: v.get("version") or 0)
    count_after = len(list_facts(cli, "--agents", ids["agent"]))
    twin = cli.run("agent", "get", ids["twin"]) or {}
    vs = [dict(version=v.get("version"), created_at=v.get("created_at"), skills=v.get("skills") or [],
               prompt=bool(v.get("system_prompt"))) for v in versions]
    lines = [f"\n  `agent version list {ids['agent']}`:"]
    for v in vs:
        sks = ", ".join(f"{s['skill_id']} v{s['skill_version']}" for s in v["skills"]) or "no skills"
        lines.append(f"    v{v['version']}  {str(v['created_at'])[:19]}Z  {sks}")
    lines.append(f"\n  Tern's memory: {count_before} fact(s) before the new version, {count_after} after — memory is not configuration")
    lines.append(f"  Twin: version {twin.get('version')}, skills {len(twin.get('skills') or [])}, memory 0 — forked from v1 before any of this")
    emit("text", "\n".join(lines))
    out = dict(skill=dict(id=skill["id"], name=skill.get("name"), version=skill.get("latest_version"), status=status,
                          waited=skill.get("waited")), lessons=lessons, markdown=md, versions=vs, created=bool(created),
               count_before=count_before, count_after=count_after,
               twin=dict(version=twin.get("version"), skills=len(twin.get("skills") or [])))
    emit("skill", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: audit trail

def audit(cli: CLI, ids: dict, story: dict, write: bool = True) -> dict:
    mem = read_memory(cli, ids, echo=False)
    events = []
    for r in mem["reflections"]:
        tr = cli.run("fact", "trace", r["id"], "--agent", ids["agent"], scoped=True) or {}
        for t in tr.get("trace") or []:
            events.append(dict(at=t.get("timestamp"), what=f"{t.get('event')} · {t.get('source_kind')}",
                               detail=f"{r['run']} lesson ({r['cause']}): {r['adjustment']}", ref=r["id"]))
    resolved = [conflict_view(cli.run("fact", "conflict", "get", c["id"], "--agent", ids["agent"], scoped=True), story)
                for c in list_conflicts(cli, ids["agent"], "--resolved", "true")]
    live = {r["id"] for r in mem["reflections"]}
    for c in resolved:
        for f in c["facts"]:
            if f["id"] in live or f["id"] in (c["resolve"] or {}).get("kept_fact_ids", []) or f["id"] in {e["ref"] for e in events}:
                continue
            # A forgotten lesson is gone from `fact list`, but its history is still there.
            tr = cli.run("fact", "trace", f["id"], "--agent", ids["agent"], scoped=True) or {}
            for t in tr.get("trace") or []:
                events.append(dict(at=t.get("timestamp"), what=f"{t.get('event')} · {t.get('source_kind')}",
                                   detail=(f"{f['run']} lesson: " if f["run"] else "extracted: ") + f["text"], ref=f["id"]))
    for c in resolved:
        rs = c["resolve"] or {}
        events.append(dict(at=c["created_at"], what=f"conflict raised · {c['category']}", detail=c["name"], ref=c["id"]))
        gone = [f for f in c["facts"] if f["id"] in (rs.get("forgotten_fact_ids") or [])]
        events.append(dict(at=rs.get("created_at"), what=f"resolved · {rs.get('strategy')}",
                           detail="forgot: " + "; ".join(f"“{f['text']}” ({f['run']})" for f in gone), ref=c["id"]))
    skill = find_skill(cli, story, echo=False)
    if skill:
        for v in (cli.run("skill", "version", "list", skill["id"]) or {}).get("items") or []:
            events.append(dict(at=v.get("created_at"), what=f"skill v{v.get('version')} · {v.get('security_status')}",
                               detail=skill.get("name"), ref=skill["id"]))
    for v in (cli.run("agent", "version", "list", ids["agent"]) or {}).get("items") or []:
        sks = ", ".join(s["skill_id"] for s in v.get("skills") or []) or "no skills"
        events.append(dict(at=v.get("created_at"), what=f"agent version {v.get('version')}", detail=sks, ref=ids["agent"]))
    events.sort(key=lambda e: e["at"] or "")
    still_open = [conflict_view(c, story) for c in list_conflicts(cli, ids["agent"], "--resolved", "false", echo=False)]
    lines = [f"\n  How Tern changed, in the order MemoryLake recorded it ({len(events)} events):"]
    for e in events:
        lines.append(f"    {str(e['at'])[:19]}Z  {e['what']:<30} {clip(e['detail'], 76)}")
    if still_open:
        lines.append(f"\n  {len(still_open)} conflict(s) still open: " + ", ".join(c["id"] for c in still_open))
    out = dict(events=events, open=[c["id"] for c in still_open], reflections=mem["reflections"])
    if write:
        md = [f"# How Tern improved", "",
              f"{story['team']} · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from MemoryLake "
              f"(agent `{ids['agent']}`, CI project `{ids['project']}`).", "", "## Lessons in memory now", ""]
        md += [f"- **{r['run']}** ({r['date']}, cause {r['cause']}) — {r['adjustment']} Expected: {r['expected']} `{r['id']}`"
               for r in mem["reflections"]]
        md += ["", "## Timeline (server time)", "", "| when | what | detail | ref |", "|---|---|---|---|"]
        md += [f"| {str(e['at'])[:19]}Z | {e['what']} | {e['detail'].replace('|', '/')} | `{e['ref']}` |" for e in events]
        md += ["", "## Still open", ""] + ([f"- `{c['id']}` {c['name']}" for c in still_open] or ["- none"])
        OUT.mkdir(exist_ok=True)
        path = OUT / "tern-improvement.md"
        path.write_text("\n".join(md) + "\n", encoding="utf-8")
        lines.append(f"\n  Audit trail written to {os.path.relpath(path)}")
        out["file"] = os.path.relpath(path)
        out["markdown"] = "\n".join(md)
    emit("text", "\n".join(lines))
    emit("audit", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, echo=not quiet)
    for r in story["runs"]:
        conv = find_conv(cli, r["run"], echo=False) if proj else None
        if conv:
            cli.run("conv", "delete", conv["id"], scoped=True)
    if proj:
        note("deleted the run conversations (`proj delete` does not delete conversations)")
    for slug in (story["agent"]["slug"], story["twin"]["slug"]):
        agent = find_agent(cli, slug, echo=not quiet)
        if not agent:
            continue
        # A deleted agent's memory stays readable but can no longer be written — or deleted. So the
        # facts go first, one call per id.
        facts = list_facts(cli, "--agents", agent["id"], echo=not quiet)
        for f in facts:
            cli.run("fact", "delete", f["id"], "--agent", agent["id"], scoped=True, echo=False)
        left = list_facts(cli, "--agents", agent["id"], echo=False)
        if left:
            die(f"{len(left)} fact(s) are still on {agent['id']}; not deleting the agent (its memory would become undeletable)")
        cli.run("agent", "delete", agent["id"])
        note(f"deleted {len(facts)} fact(s) from {agent['id']}'s memory, then the agent")
    skill = find_skill(cli, story, echo=not quiet)
    if skill:
        cli.run("skill", "delete", skill["id"])
        note("deleted the skill and its versions")
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the CI project (the incident facts go with it)")
    human = find_actor(cli, story["human"]["slug"], echo=not quiet)
    if human:
        cli.run("actor", "delete", human["id"])
        note(f"deleted {story['human']['name']}")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Agent, twin, CI project — Tern gets a memory of its own; the twin is a fork")
    ids = setup(cli, story, reset)
    banner(3, TOTAL, "Five runs Tern got wrong — and the reflection it wrote after each one")
    rs = runs_step(cli, ids, story)
    banner(4, TOTAL, "What Tern remembers — lessons on the agent, incidents on the project")
    mem = remembers(cli, ids, story)
    banner(5, TOTAL, "Two lessons disagree — MemoryLake flags it on the agent; keep_fact settles it")
    det = detector(cli, ids, story, rs["reflections"])
    res = resolve(cli, ids, story, det)
    banner(6, TOTAL, f"Today's failures — Tern plans from its lessons, the twin from nothing")
    td = today(cli, ids, story)
    banner(7, TOTAL, "A pattern across reflections becomes a skill — and agent version 2")
    sk = skill_step(cli, ids, story)
    banner(8, TOTAL, "Audit trail — how Tern's behavior changed, and why")
    au = audit(cli, ids, story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   cook_seconds=rs["cook_seconds"], reflections=len(mem["reflections"]), extracted_on_agent=len(mem["extracted"]),
                   project_facts=len(mem["project"]), fresh=det["fresh"], waited=det["waited"],
                   detected=len(det["conflicts"]), others=len(det["others"]), resolved=len(res["resolved"]),
                   before=det["before"]["verdict"], after=res["after"]["verdict"],
                   today={t["key"]: dict(tern=t["tern"]["top"], ok=t["tern"]["ok"], twin_hits=len(t["twin"]["hits"])) for t in td},
                   skill=(sk.get("skill") or {}).get("status"), versions=len(sk.get("versions") or []),
                   memory_same=sk.get("count_before") == sk.get("count_after"), events=len(au["events"]))
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj, agent, twin = find_project(cli), find_agent(cli, story["agent"]["slug"]), find_agent(cli, story["twin"]["slug"])
    human = find_actor(cli, story["human"]["slug"])
    if not (proj and agent and twin and human):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], human=human["id"], agent=agent["id"], agent_actor=agent["actor_id"],
                twin=twin["id"], twin_actor=twin["actor_id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "reflections", "plan", "audit", "cleanup"],
                    help="run = full demo (default); reflections = what Tern remembers; plan \"FAILURE\" = Tern's and the "
                         "twin's plan for a failure; audit = how Tern changed; cleanup = delete everything")
    ap.add_argument("task", nargs="?", help="the failure to plan for, for `plan`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "plan" and not args.task:
        ap.error("plan needs a failure, e.g. python3 demo.py plan \"test_invoice_pdf timed out in CI\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py reflections`, `python3 demo.py plan \"…\"` or `python3 demo.py audit`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    story, ids = load_story(), need_ids(cli)
    if args.command == "reflections":
        banner(2, 2, "What Tern remembers")
        remembers(cli, ids, story)
    elif args.command == "plan":
        banner(2, 2, "Plan a failure from memory — Tern vs the twin")
        show_plan(plan(cli, ids, story, args.task), "Tern:")
        show_plan(plan(cli, ids, story, args.task, who="twin"), "Twin:")
    elif args.command == "audit":
        banner(2, 2, "How Tern changed")
        items = [conflict_view(c, story) for c in list_conflicts(cli, ids["agent"])]
        if items:
            show_conflicts(items, f"\n  {len(items)} conflict(s) on Tern's memory:")
        audit(cli, ids, story)


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

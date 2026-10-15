#!/usr/bin/env python3
"""
Follow-up chat memory: a chatbot's memory lookup has to read the conversation,
not just the latest message — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Hearth (fictional) is a family-assistant chatbot.  Over earlier chats Dana told
it about her family: Leo (9), Maya (6), her husband Sam, her mother-in-law Nora
and the dog Biscuit — allergies, doctors, bedtimes, birthdays, 65 facts in one
MemoryLake project, with one *boundary* over it.

Tonight Dana asks seven quick questions in one chat.  Only the first names who
it is about: "What is Leo allergic to?" — "And Maya?" — "Who's her doctor?" …

What it shows
-------------
  · Lookup on the latest message (the usual do-it-yourself chatbot: search
    with what the user just typed, paste the top hits into the prompt): "And
    Maya?" and "Who's her doctor?" do not say what they are about, the lookup
    brings back someone else's facts, and Hearth says it doesn't know — about
    things it was told.
  · Lookup on the last few turns (the usual fix): the follow-ups work, but now
    the earlier topic drags along when Dana changes subject.
  · MemoryRouter, with the conversation in the request body (`router chat
    --body`) and the boundary: its retrieval reads the conversation, so each
    follow-up gets the fact about the person it points back to.
  · Why: a checker (Claude, asked to copy out everything above the request)
    shows what MemoryRouter attached on each follow-up.

Model wording varies run to run; every reply is graded and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (21 calls
on gpt-4o-mini and 3 on claude-haiku-4-5 per run).
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-fcm"  # memorylake-usecases / followup-chat-memory

SYSTEM = ("You are Hearth, Dana's family assistant. Answer in one short sentence using what you remember about "
          "Dana's family. If you do not know, say so.")


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




MODEL = dict(protocol="chat", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Chat Completions)")
CHECKER = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")
TOP_K = 3  # what the do-it-yourself lookups paste into the prompt
WINDOW = 2  # the sliding-window lookup: this many earlier user turns + the latest one

ARMS = {
    "last": dict(label="Lookup on the latest message", tag="the usual do-it-yourself chatbot",
                 how="`memorylake search \"<latest message>\"`, top 3 pasted into the system prompt"),
    "window": dict(label="Lookup on the last few turns", tag="the usual fix",
                   how=f"the last {WINDOW} user turns + the latest message as one query, top 3 pasted in"),
    "router": dict(label="MemoryRouter", tag="the conversation goes in",
                   how="`router chat --body <conversation so far> --boundary <id>`: the conversation goes in, "
                       "MemoryRouter picks the memory"),
}


# --------------------------------------------------------------------------- data

def load_family() -> dict:
    return json.loads((DATA / "family.json").read_text(encoding="utf-8"))


def fact_texts(fam: dict) -> list[str]:
    out = [f"{m['name']} is Dana's {m['role']}." for m in fam["members"]]
    for i, m in enumerate(fam["members"]):
        for a in fam["attributes"]:
            out.append(a["template"].format(name=m["name"], value=a["values"][i]))
    return out


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def grade(turn: dict, reply: str) -> bool:
    low = reply.lower()
    return any(e in low for e in turn["expect"])


def is_target(turn: dict, fam: dict, line: str) -> bool:
    """Is this memory line the one the turn needs (the right person's value for the right attribute)?"""
    i = [m["name"] for m in fam["members"]].index(turn["about"])
    a = next(a for a in fam["attributes"] if a["key"] == turn["attr"])
    return line.strip().rstrip(".") == a["template"].format(name=turn["about"], value=a["values"][i]).rstrip(".")


def whose(line: str, fam: dict) -> str:
    for m in fam["members"]:
        if line.startswith(m["name"]):
            return m["name"]
    return "?"


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("family"), "--by-custom-id", scoped=True, echo=echo)


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


def list_facts(cli: CLI, project: str, query: str | None = None, echo: bool = True) -> list[dict]:
    """Every fact (or every fact whose text contains `query`), page by page until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--projects", project, "--page-size", "200"]
        if query:
            args += ["--query", query]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def setup(cli: CLI) -> dict:
    old = find_project(cli, echo=True)
    if old is not None:
        n = len(list_facts(cli, old["id"], echo=False))
        note(f"an earlier run left Hearth's memory of {n} facts; starting over with a fresh project "
             "(a new scope, so no lookup can be answered from an earlier run's cache)")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Hearth: Dana's family", "--custom-id", cid("family"),
                   "--description", "What a family-assistant chatbot remembers about one user. Demo data.", scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: the chatbot passes this boundary to MemoryRouter and nothing else.
    b = cli.run("boundary", "create", "--name", "Hearth for Dana", "--custom-id", cid("boundary"),
                "--projects", proj["id"], scoped=True)
    note(f"created boundary {b['id']} over the project")
    world = dict(project=proj["id"], boundary=b["id"])
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    proj = find_project(cli)
    b = {x["custom_id"]: x for x in demo_boundaries(cli)}.get(cid("boundary"))
    if not (proj and b):
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], boundary=b["id"])


# --------------------------------------------------------------------------- step 3: what Hearth remembers

def store(cli: CLI, world: dict) -> list[str]:
    fam = load_family()
    want = fact_texts(fam)
    batches = [want[i:i + 25] for i in range(0, len(want), 25)]
    shown = ["fact", "add", "--project", world["project"], *want[:2]]
    emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace]) + f"  … {len(want) - 2} more")

    def add(batch: list[str]) -> bool:
        try:
            cli.run("fact", "add", "--project", world["project"], *batch, scoped=True, echo=False)
            return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
            return False

    with ThreadPoolExecutor(3) as pool:
        ok = list(pool.map(add, batches))
    if not all(ok):
        # `fact add` is not idempotent, so a dropped call is not retried blindly: add only what is still missing.
        have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
        missing = [t for t in want if t not in have]
        note(f"{ok.count(False)} `fact add` call(s) hit a network error; adding the {len(missing)} fact(s) still missing")
        for i in range(0, len(missing), 25):
            cli.run("fact", "add", "--project", world["project"], *missing[i:i + 25], scoped=True, echo=False)
    facts = [f["fact"] for f in list_facts(cli, world["project"])]
    if sorted(facts) != sorted(want):
        die(f"the project holds {len(facts)} facts, expected {len(want)}")
    note(f"stored {len(facts)} facts in {len(batches)} `fact add` call(s): {len(fam['members'])} family members × "
         f"{len(fam['attributes'])} things Dana has mentioned, plus who is who")
    emit("text", "\n  A few of them:\n" + "\n".join(f"   - {t}" for t in (want[:2] + [want[6], want[19], want[30]])))
    emit("memory", "", facts=len(facts), members=[m["name"] for m in fam["members"]],
         attributes=[a["key"] for a in fam["attributes"]])
    time.sleep(10)  # a new fact is in `fact list` at once; retrieval takes a few seconds to see it
    return facts


# --------------------------------------------------------------------------- the three ways to bring memory into a chat

def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, tool: dict = MODEL,
           max_tokens: int = 200, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. `body` holds the system message and the conversation so far."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = [tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", str(max_tokens),
            "--temperature", "0", "--body", str(body.relative_to(HERE))]
    if boundary:
        args += ["--boundary", boundary]
    t0 = time.time()
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model replies below are skipped; the lookups still show what each approach retrieves. "
                 "Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    secs = time.time() - t0
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    if tool["protocol"] == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct, seconds=round(secs, 1))


def lookup(cli: CLI, world: dict, query: str, echo: bool = True) -> list[str]:
    res = cli.run("search", query, "--projects", world["project"], "--types", "fact", "--top-k", str(TOP_K),
                  scoped=True, echo=echo) or {}
    return [f.get("fact") or "" for f in res.get("facts") or []][:TOP_K]


def write_body(name: str, system: str, history: list[dict], tool: dict = MODEL) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    if tool["protocol"] == "chat":
        body = {"model": tool["model"], "messages": [{"role": "system", "content": system}] + history}
    else:
        body = {"model": tool["model"], "system": system, "messages": history}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def query_for(arm: str, history: list[dict], say: str) -> str:
    if arm == "last":
        return say
    users = [m["content"] for m in history if m["role"] == "user"][-WINDOW:]
    return " ".join(users + [say])


def chat(cli: CLI, world: dict, arm: str, quiet: bool = False) -> list[dict]:
    """Dana's seven-turn chat, through one arm. Each arm keeps its own history (its own replies)."""
    fam = load_family()
    history: list[dict] = []
    turns = []
    for n, t in enumerate(fam["chat"], 1):
        echo = not quiet and n <= 2
        memory = None
        if arm == "router":
            body = write_body(arm, SYSTEM, history)
            r = router(cli, t["say"], body, boundary=world["boundary"], echo=echo)
        else:
            q = query_for(arm, history, t["say"])
            memory = lookup(cli, world, q, echo=echo)
            system = SYSTEM + "\n\nWhat you remember about Dana's family:\n" + "\n".join("- " + m for m in memory)
            body = write_body(arm, system, history)
            r = router(cli, t["say"], body, echo=echo)  # no boundary: the model sees only what the lookup pasted in
        reply = r["reply"] if r else ""
        ok = grade(t, reply) if r else None
        found = None if memory is None else any(is_target(t, fam, m) for m in memory)
        row = dict(arm=arm, turn=n, key=t["key"], say=t["say"], about=t["about"], reply=reply, ok=ok,
                   memory=memory, found=found, query=None if arm == "router" else query_for(arm, history, t["say"]))
        turns.append(row)
        if not quiet:
            show_turn(row)
        emit("turn", "", **row)
        history += [{"role": "user", "content": t["say"]}, {"role": "assistant", "content": reply or "(no reply)"}]
        if n == 2 and not quiet:
            note(f"the remaining turns are sent the same way ({len(history) // 2} earlier turns now in --body)"
                 if arm == "router" else "the remaining turns are looked up and sent the same way")
    return turns


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


def show_turn(row: dict) -> None:
    lines = [f"\n    Dana: {row['say']}"]
    if row["memory"] is not None:
        if row["arm"] == "window":
            lines.append(f"      lookup query: “{textwrap.shorten(row['query'], 80)}”")
        top = row["memory"][0] if row["memory"] else "(nothing)"
        lines.append(f"      lookup #1: {textwrap.shorten(top, 70)}"
                     + ("" if row["found"] else "   ← the fact this turn needs is not in the top 3"))
    if row["ok"] is not None:
        lines.append(f"    {mark(row['ok'])} Hearth: {textwrap.shorten(row['reply'], 92)}")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 7: what MemoryRouter attached

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). Then answer the question.")


def attached(cli: CLI, world: dict, history: list[dict], say: str, stored: list[str]) -> dict | None:
    """Ask Claude the same turn, with the same conversation, through the same boundary, told (in the system prompt)
    to copy out first every memory MemoryRouter attached. The conversation is unchanged, so retrieval sees what it saw."""
    body = write_body("checker", CHECK_SYSTEM, history, tool=CHECKER)
    r = router(cli, say, body, boundary=world["boundary"], tool=CHECKER, max_tokens=1500, echo=False)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    lines = [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower().rstrip(".")
    known = {norm(t) for t in stored}
    return dict(lines=lines, unmatched=[ln for ln in lines if norm(ln) not in known])


def history_of(turns: list[dict], upto: int) -> list[dict]:
    h = []
    for t in turns[:upto]:
        h += [{"role": "user", "content": t["say"]}, {"role": "assistant", "content": t["reply"] or "(no reply)"}]
    return h


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
FOLLOWUPS = ("t2", "t3", "t7")  # the turns step 7 looks inside


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    fam = load_family()
    banner(2, TOTAL, "Set up — one memory project and one boundary the chatbot passes to MemoryRouter")
    world = setup(cli)
    emit("text", f"\n  Hearth is a family-assistant chatbot. Over the past weeks Dana told it about her family:\n"
         f"  {', '.join(m['name'] + ' (' + m['role'] + ')' for m in fam['members'])}.")

    banner(3, TOTAL, "What Hearth remembers — earlier chats, stored as facts")
    stored = store(cli, world)
    emit("text", "\n  Tonight Dana opens a new chat and asks seven quick questions:\n"
         + "\n".join(f"    {n}. {t['say']}" for n, t in enumerate(fam["chat"], 1))
         + "\n  Only the first one names who it is about. Every answer is in memory.")

    runs: dict[str, list[dict]] = {}
    for step, arm in ((4, "last"), (5, "window"), (6, "router")):
        a = ARMS[arm]
        banner(step, TOTAL, f"{a['label']} — {a['tag']}")
        note(a["how"])
        runs[arm] = chat(cli, world, arm)
        got = [t for t in runs[arm] if t["ok"] is not None]
        if got:
            emit("text", f"\n    {sum(t['ok'] for t in got)}/{len(got)} answers right")
        if arm != "router":
            missed = [t for t in runs[arm] if not t["found"]]
            note(f"the fact a turn needed was missing from the lookup on {len(missed)} of {len(runs[arm])} turns"
                 + (": " + ", ".join(f"“{t['say']}”" for t in missed) if missed else ""))
            wrong = [t for t in missed if t["memory"] and whose(t["memory"][0], fam) != t["about"]]
            if wrong:
                note("on " + ", ".join(f"“{t['say']}”" for t in wrong) + " the top hit was about someone else ("
                     + ", ".join(whose(t["memory"][0], fam) for t in wrong) + ")")
        time.sleep(2)

    banner(7, TOTAL, "Why — what MemoryRouter attached to the follow-ups (Claude copies out what sits above the request)")
    checks = []
    if runs.get("router") and cli.router_ok:  # type: ignore[attr-defined]
        rt = runs["router"]
        note("the same conversation, the same boundary, sent to claude-haiku-4-5 with an instruction to copy out "
             "the memory first")
        for key in FOLLOWUPS:
            i = next(n for n, t in enumerate(rt) if t["key"] == key)
            c = attached(cli, world, history_of(rt, i), rt[i]["say"], stored)
            if c is None:
                break
            t = fam["chat"][i]
            hit = any(is_target(t, fam, ln) for ln in c["lines"])
            lk = runs["last"][i]["memory"] or []
            row = dict(key=key, say=t["say"], about=t["about"], lines=c["lines"], target=hit,
                       unmatched=c["unmatched"], lookup=lk)
            checks.append(row)
            emit("text", f"\n    Dana: {t['say']}   (about {t['about']})\n"
                 f"      latest-message lookup got: " + "; ".join(textwrap.shorten(x, 40) for x in lk) + "\n"
                 f"      MemoryRouter attached {len(c['lines'])}: " + "; ".join(c["lines"])
                 + ("" if hit else "   ← not the fact this turn needs"))
            if c["unmatched"]:
                note(f"{len(c['unmatched'])} copied line(s) do not match a stored fact word for word")
            emit("inspect", "", **row)
        if checks:
            note(f"MemoryRouter's retrieval reads the conversation, not only the last line: on {sum(c['target'] for c in checks)}"
                 f" of {len(checks)} follow-ups it attached the fact about the person the follow-up points back to")
    else:
        note("skipped: needs MemoryRouter")

    banner(8, TOTAL, "Scoreboard — the same seven questions, the same model; only how memory gets in differs")
    lines = [f"\n    {'Dana asks':<48}{'latest msg':>11}{'last turns':>11}{'Router':>8}"]
    rows = []
    for i, t in enumerate(fam["chat"]):
        cells = {arm: runs[arm][i]["ok"] for arm in runs}
        rows.append(dict(key=t["key"], say=t["say"], **{arm: cells.get(arm) for arm in ARMS}))
        lines.append(f"    {textwrap.shorten(t['say'], 46):<48}{mark(cells.get('last')):>11}{mark(cells.get('window')):>11}"
                     f"{mark(cells.get('router')):>8}")
    tot = {arm: sum(1 for t in runs[arm] if t["ok"]) for arm in runs}
    lines.append(f"    {'':<48}{tot.get('last', 0):>9}/7{tot.get('window', 0):>9}/7{tot.get('router', 0):>6}/7")
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=rows, totals=tot)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(runs=runs, checks=checks, totals=tot, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every turn, lookup and reply: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def replay(cli: CLI, arm: str) -> list[dict]:
    world = load_world(cli)
    note(ARMS[arm]["how"])
    turns = chat(cli, world, arm)
    got = [t for t in turns if t["ok"] is not None]
    if got:
        emit("text", f"\n    {ARMS[arm]['label']}: {sum(t['ok'] for t in got)}/{len(got)} answers right")
    return turns


def compare_turn(cli: CLI, history: list[dict], say: str) -> dict:
    """One message of a free-form chat (the web page's Chat view), answered two ways: with a lookup on the latest
    message, and through MemoryRouter with the conversation. Both get the same history (MemoryRouter's replies)."""
    world = load_world(cli)
    history = [{"role": m["role"], "content": str(m["content"])} for m in history
               if m.get("role") in ("user", "assistant")][-20:]
    memory = lookup(cli, world, say)
    system = SYSTEM + "\n\nWhat you remember about Dana's family:\n" + "\n".join("- " + m for m in memory)
    a = router(cli, say, write_body("ask-last", system, history))
    b = router(cli, say, write_body("ask-router", SYSTEM, history), boundary=world["boundary"])
    if not (a and b):
        die("MemoryRouter is not available on this account (no balance)")
    return dict(say=say, last=dict(memory=memory, reply=a["reply"]), router=dict(reply=b["reply"], seconds=b["seconds"]))


# --------------------------------------------------------------------------- cleanup

def delete_fact(cli: CLI, fact_id: str, project: str) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, "--project", project, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
        if not quiet:
            note(f"deleted boundary {b['name']}")
    proj = find_project(cli, echo=not quiet)
    if proj:
        facts = list_facts(cli, proj["id"], echo=False)
        left = facts
        for attempt in range(4):  # a list right after many deletes can still show a few: delete those and look again
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], proj["id"]), left))
            time.sleep(2 * attempt)
            left = list_facts(cli, proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        if not quiet:
            note(f"deleted {len(facts)} fact(s) and the project")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "chat", "cleanup"],
                    help="run = full demo (default); chat = replay Dana's seven questions through one approach; "
                         "cleanup = delete everything")
    ap.add_argument("--arm", default="router", choices=list(ARMS),
                    help="for chat: router (default), last (lookup on the latest message) or window (last few turns)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
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
    if args.command == "chat":
        banner(2, 2, f"Dana's seven questions again — {ARMS[args.arm]['label']}")
        replay(cli, args.arm)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py chat --arm last` or `python3 demo.py chat`; '
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

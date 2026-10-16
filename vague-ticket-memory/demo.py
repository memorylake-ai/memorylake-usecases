#!/usr/bin/env python3
"""vague-ticket-memory — when a customer's message names no topic, MemoryRouter can attach nothing at all.

Brightdesk (a fictional 9-person helpdesk SaaS) keeps its support policies in shared memory: 20 facts in one
project, one boundary. Tier 1 drafts replies with MemoryRouter — an ordinary model call with `--boundary` added,
and MemoryRouter picks which policies to attach.

Customers rarely name the topic. "Please wipe everything you have on us." "I want it back. All of it." "you did
it twice!!" For messages like these MemoryRouter often attaches nothing: the call's `usage.prompt_tokens` is
exactly what the same call costs without a boundary. The reply does not say so. It reads just as sure as a
grounded one, and the policy in it is made up (no 14-day grace period, no 30-day refund window).

The fix uses something the help widget already has: the topic the customer picked before writing
("Account & data › Close account"). Send it with the message and the policies get attached. A guard catches the
rest: when `prompt_tokens` equals the no-memory baseline, hold the draft instead of sending it.

Each message is asked 3 times per wiring; what MemoryRouter attaches varies run to run, so the README reports
totals over all runs. Two control messages say the same thing with the topic named ("Please delete our account.").

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 110 calls on gpt-4o-mini per run, about 10,000 tokens).
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-vtm"  # memorylake-usecases / vague-ticket-memory


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




MODEL = "gpt-4o-mini"
RUNS = 3  # calls per message and wiring
ARMS = ("plain", "topic")
ARM_LABEL = {"plain": "message only", "topic": "message + widget topic"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "brightdesk.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def policy_text(d: dict, key: str) -> str:
    return next(p["text"] for p in d["policies"] if p["key"] == key)


def pick_message(d: dict, mid: str) -> dict:
    for m in d["inbox"]:
        if m["id"].lower() == mid.lower():
            return m
    die(f"unknown message {mid!r}; one of: {', '.join(m['id'] for m in d['inbox'])}")


def prompt_for(message: str, topic: str | None) -> str:
    """What the integration sends as the user turn: the customer's message, plus — in the second wiring — one line
    with the topic they picked in the help widget. Nothing else differs."""
    if topic:
        return f"[Topic picked in the help widget: {topic}]\n{message}"
    return message


GERMAN = re.compile(r"\b(Sie|Ihre?[nmr]?|Ihnen|Ich|und|werde|Vielen Dank|Sehr geehrte|Mit freundlichen|bitte|wir|Konto)\b", re.I)


def grade(reply: str, m: dict) -> tuple[bool, str]:
    """Right = the reply states the policy value the message needs (one pattern per message), in English. A reply in
    German to an English message is wrong whatever it says: the Germany-only "Sie" rule was attached and obeyed."""
    reply = reply or ""
    if len(GERMAN.findall(reply)) >= 3:
        return False, "answered in German (the Germany-only Sie rule was attached)"
    if re.search(m["right"], reply, re.I):
        return True, ""
    return False, "does not state: " + m["right_label"]


def mark(r: dict) -> str:
    if r["ok"] is None:
        return "·"
    return "∅" if r["empty"] else ("✓" if r["ok"] else "✗")


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r["ok"] is not None]
    return dict(calls=len(live), right=sum(1 for r in live if not r["empty"] and r["ok"]),
                wrong=sum(1 for r in live if not r["empty"] and not r["ok"]),
                empty=sum(1 for r in live if r["empty"]),
                empty_right=sum(1 for r in live if r["empty"] and r["ok"]))


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("policies"), "--by-custom-id", scoped=True, echo=echo)


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
    """Every fact in the project, page by page until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--page-size", "200", "--projects", project]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or find_project(cli):
        note("an earlier run left Brightdesk's memory behind; starting over")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Brightdesk support policies", "--custom-id", cid("policies"),
                   "--description", "Brightdesk's shared support policies. Demo data.", scoped=True)
    b = cli.run("boundary", "create", "--name", "Brightdesk Tier 1 assistant", "--custom-id", cid("tier1"),
                "--projects", proj["id"], scoped=True)
    note(f"the Tier 1 assistant's boundary {b['id']} covers the project: every draft is `router chat … --boundary` "
         "and MemoryRouter picks which policies to attach")
    world = dict(project=proj["id"], boundary=b["id"])
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    proj = find_project(cli)
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    if not proj or cid("tier1") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], boundary=bs[cid("tier1")])


# --------------------------------------------------------------------------- step 3: the policies

def store(cli: CLI, world: dict) -> None:
    d = load_data()
    texts = [p["text"] for p in d["policies"]]
    more = f"  … {len(texts) - 2} more"
    emit("cmd", shlex.join(["memorylake", "fact", "add", "--project", world["project"], *texts[:2],
                            "--workspace", cli.workspace]) + more)
    try:
        cli.run("fact", "add", "--project", world["project"], *texts, scoped=True, echo=False)
    except CLIError as e:
        # `fact add` is not idempotent: after a network error, add only what is missing
        if not e.has(*CLI.TRANSIENT):
            raise
        have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
        missing = [t for t in texts if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", "--project", world["project"], *missing, scoped=True, echo=False)
    facts = list_facts(cli, world["project"])
    if sorted(f["fact"] for f in facts) != sorted(texts):
        die(f"the project holds {len(facts)} facts, expected {len(texts)}")
    note(f"{len(facts)} policies stored word for word, one fact each")
    show = ("deletion", "refund-annual", "duplicate", "sso", "trial")
    emit("text", "\n" + "\n".join(f"    - {policy_text(d, k)}" for k in show) + f"\n    … {len(facts) - len(show)} more")
    emit("memory", "", count=len(facts), policies=d["policies"])


# --------------------------------------------------------------------------- step 4: the inbox

def show_inbox(d: dict) -> None:
    emit("text", f"    {'':<4}{'customer message':<56}{'topic they picked in the help widget':<36}")
    for m in d["inbox"]:
        if m["id"] == "C1":
            emit("text", "    controls — the same requests with the topic named:")
        emit("text", f"    {m['id']:<4}{json.dumps(m['message'], ensure_ascii=False):<56}{m['topic']}")
    emit("text", "\n    a right reply states the policy value (graded with one pattern per message), e.g.\n"
         + "\n".join(f"    {m['id']}  {m['right_label']}" for m in d["inbox"][:3]))
    emit("inbox", "", messages=d["inbox"])


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, boundary: str | None = None, max_tokens: int = 160, echo: bool = False) -> dict | None:
    """One `router chat` call. Without a boundary it is the plain model call — the baseline."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    d = load_data()
    args = ["chat", prompt, "--model", MODEL, "--max-tokens", str(max_tokens), "--temperature", "0", "--system", d["system"]]
    if boundary:
        args += ["--boundary", boundary]
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model replies below are skipped; the memory steps still ran. Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    with LOCK:
        cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
        cli.tokens["completion"] += ct  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=(res["choices"][0]["message"]["content"] or "").strip(), prompt_tokens=pt)


LOCK = threading.Lock()


def ask(cli: CLI, world: dict, message: str, topic: str | None, runs: int = RUNS, echo_first: bool = False) -> dict:
    """The no-memory baseline once, then `runs` calls with the boundary. attached = prompt tokens − baseline."""
    prompt = prompt_for(message, topic)
    base = router(cli, prompt, max_tokens=1, echo=echo_first)
    if not base:
        return dict(base=0, calls=[])
    calls = []
    if echo_first:
        calls.append(router(cli, prompt, boundary=world["boundary"], echo=True))
        runs -= 1
    with ThreadPoolExecutor(max(1, min(4, runs))) as pool:
        calls += list(pool.map(lambda _: router(cli, prompt, boundary=world["boundary"]), range(runs)))
    out = []
    for c in calls:
        if not c:
            continue
        out.append(dict(reply=c["reply"], prompt_tokens=c["prompt_tokens"], attached=c["prompt_tokens"] - base["prompt_tokens"],
                        empty=c["prompt_tokens"] == base["prompt_tokens"]))
    return dict(base=base["prompt_tokens"], calls=out)


def ask_arm(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    rows = []
    for n, m in enumerate(d["inbox"]):
        got = ask(cli, world, m["message"], m["topic"] if arm == "topic" else None, echo_first=n == 0)
        if n == 0 and got["calls"]:
            note(f"first the same call without --boundary ({got['base']} prompt tokens: system + message, no memory), "
                 f"then {RUNS} with it; attached = the difference. The other messages run the same way, quietly.")
        mine = []
        for i, c in enumerate(got["calls"]):
            ok, why = grade(c["reply"], m)
            mine.append(dict(m=m["id"], arm=arm, run=i + 1, base=got["base"], ok=ok, why=why, control=m["control"], **c))
        if not mine:
            mine = [dict(m=m["id"], arm=arm, run=0, base=0, ok=None, why="", control=m["control"], reply="", prompt_tokens=0, attached=0, empty=False)]
        rows += mine
        if m["id"] == "C1":
            emit("text", "    controls:")
        att = " · ".join(f"+{r['attached']}" for r in mine if r["ok"] is not None) or "–"
        emit("text", f"    {m['id']:<4}{' '.join(mark(r) for r in mine):<8} attached {att:<20} {m['message']}")
        emit("ask_row", "", m=m["id"], arm=arm, rows=mine)
    return rows


def wrap(text: str, indent: str = "      ") -> str:
    return "\n".join(textwrap.wrap(text, 96, initial_indent=indent, subsequent_indent=indent))


def show_examples(rows: list[dict], arm: str) -> list[dict]:
    """The replies a customer would have received from an empty call (step 5), or the same messages answered with
    their topic (step 6)."""
    d = load_data()
    picked = []
    if arm == "plain":
        seen = set()
        for r in rows:
            if r["empty"] and not r["ok"] and r["m"] not in seen and len(picked) < 3:
                seen.add(r["m"])
                picked.append(r)
        if not picked:
            emit("text", "\n    no call came back empty this run (it varies run to run; see the README for totals)")
        else:
            emit("text", "\n    replies drafted with nothing attached — what would have been sent:")
        wrong = next((r for r in rows if not r["empty"] and r["ok"] is False), None)
    else:
        want = [e["m"] for e in EXAMPLES.get("plain", [])] or ["M1", "M2"]
        for mid in want:
            r = next((x for x in rows if x["m"] == mid and not x["empty"]), None)
            if r:
                picked.append(r)
        if not picked:
            return []
        emit("text", "\n    the same messages, now with their topic:")
    if arm == "plain" and wrong:
        picked_all = picked + [wrong]
    else:
        picked_all = picked
    for r in picked_all:
        m = pick_message(d, r["m"])
        if r is not None and arm == "plain" and r is wrong:
            emit("text", "\n    and one drafted with a policy attached — the wrong one:")
        verdict = "✓" if r["ok"] else "✗"
        emit("text", f"    {r['m']} “{m['message']}” — attached +{r['attached']} tokens, {verdict} (needs: {m['right_label']})\n" + wrap(r["reply"])
             + ("" if r["ok"] or not r.get("why", "").startswith("answered") else "\n      ← " + r["why"]))
    EXAMPLES[arm] = picked_all
    emit("examples", "", arm=arm, rows=picked_all)
    return picked


EXAMPLES: dict[str, list[dict]] = {}


def arm_line(rows: list[dict], label: str) -> str:
    t = tally([r for r in rows if not r["control"]])
    c = tally([r for r in rows if r["control"]])
    return (f"    {label:<24} vague {t['right']} ✓ · {t['wrong']} ✗ · {t['empty']} ∅ of {t['calls']}"
            f"     controls {c['right']} ✓ · {c['wrong']} ✗ · {c['empty']} ∅ of {c['calls']}")


# --------------------------------------------------------------------------- step 7: the score and the guard

def score(rows: list[dict]) -> dict:
    out = {}
    lines = []
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        out[arm] = dict(vague=tally([r for r in mine if not r["control"]]), control=tally([r for r in mine if r["control"]]),
                        all=tally(mine))
        lines.append(arm_line(mine, ARM_LABEL[arm]))
    emit("text", "\n".join(lines))
    emit("text", "\n    ✓ right · ✗ wrong · ∅ nothing attached (prompt tokens = the no-memory baseline): the reply is the model "
         "on its own, graded apart")
    lucky = sum(out[a]["all"]["empty_right"] for a in ARMS)
    if lucky:
        note(f"{lucky} of the ∅ replies happened to state the right value anyway; they are still counted as ∅")
    emit("text", "\n    The guard — hold a draft when it was written with nothing attached (attached = +0 tokens):")
    for arm in ARMS:
        a = out[arm]["all"]
        emit("text", f"    {ARM_LABEL[arm]:<24} held {a['empty']:>2} · sent {a['right'] + a['wrong']:>2} "
                     f"({a['right']} right, {a['wrong']} wrong)")
    note("the guard catches every empty call, because the prompt-token count gives it away. It cannot catch a wrong "
         "answer written with the wrong policy attached — that one has tokens attached like any other")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    EXAMPLES.clear()
    banner(2, TOTAL, "Set up — Brightdesk's shared policies: one project, one boundary")
    world = setup(cli)

    banner(3, TOTAL, f"What the team knows — {len(d['policies'])} support policies")
    store(cli, world)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`

    banner(4, TOTAL, "The inbox — what customers actually write")
    show_inbox(d)

    banner(5, TOTAL, "Draft replies from the message only — as most integrations wire it")
    rows = ask_arm(cli, world, "plain")
    plain = [r for r in rows if r["ok"] is not None]
    if plain:
        emit("text", "\n" + arm_line(rows, ARM_LABEL["plain"]))
        show_examples(rows, "plain")

    banner(6, TOTAL, "Draft replies with the topic the customer picked in the help widget")
    rows2 = ask_arm(cli, world, "topic")
    if [r for r in rows2 if r["ok"] is not None]:
        emit("text", "\n" + arm_line(rows2, ARM_LABEL["topic"]))
        show_examples(rows2, "topic")

    banner(7, TOTAL, "The score — and a guard for what is left")
    sc = score(rows + rows2) if plain else {}

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows + rows2, score=sc, tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every call, reply and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def attach(cli: CLI, message: str, topic: str | None, runs: int = RUNS) -> dict:
    """Any message, through the demo's boundary: how much memory was attached, call by call, and the replies."""
    world = load_world(cli)
    got = ask(cli, world, message, topic, runs=runs, echo_first=True)
    if not got["calls"]:
        die("MemoryRouter is not available on this account (no balance)")
    emit("text", f"\n  “{message}”" + (f"  · topic: {topic}" if topic else "") + f"\n  baseline (no boundary): {got['base']} prompt tokens")
    for i, c in enumerate(got["calls"], 1):
        emit("text", f"  call {i}: {c['prompt_tokens']} prompt tokens → attached +{c['attached']}"
             + ("   ∅ nothing attached" if c["empty"] else "") + "\n" + wrap(c["reply"]))
    emit("attach", "", message=message, topic=topic, **got)
    return got


# --------------------------------------------------------------------------- cleanup

def delete_scoped(cli: CLI, fact_id: str, project: str) -> None:
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
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    proj = find_project(cli, echo=not quiet)
    if proj:
        facts = list_facts(cli, proj["id"], echo=False)
        left = facts
        for attempt in range(4):  # a list right after many deletes can still show a few: delete those and look again
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_scoped(cli, f["id"], proj["id"]), left))
            time.sleep(2 * attempt)
            left = list_facts(cli, proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        if not quiet:
            note(f"deleted the boundary, the project and its {len(facts)} fact(s)")
    elif not quiet:
        note("nothing to delete")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "inbox", "attach", "cleanup"],
                    help="run = full demo (default); inbox = the customer messages (offline); "
                         "attach \"<message>\" = how much memory MemoryRouter attaches to any message; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for attach: the message (or an inbox id such as M1)")
    ap.add_argument("--topic", help="for attach: the help-widget topic to send with the message")
    ap.add_argument("--runs", type=int, default=RUNS, help="for attach: calls with the boundary (default 3)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "inbox":
        banner(1, 1, "The inbox — offline, no API calls")
        show_inbox(load_data())
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "attach":
        if len(args.args) != 1:
            die('usage: python3 demo.py attach "<message>" [--topic "<topic>"]   (or an inbox id: attach M1)')
        msg, topic = args.args[0], args.topic
        if re.fullmatch(r"[MC]\d+", msg, re.I):
            m = pick_message(load_data(), msg)
            msg = m["message"]
        banner(2, 2, "How much memory MemoryRouter attaches to one message")
        attach(cli, msg, topic, runs=max(1, args.runs))
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py attach M1`, `python3 demo.py attach M1 --topic "Account & data › Close account"`, '
          "or any message of your own; `python3 demo.py cleanup` removes the demo data.")


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

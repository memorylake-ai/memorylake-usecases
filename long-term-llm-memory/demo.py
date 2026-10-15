#!/usr/bin/env python3
"""
Long-term memory for any LLM, without bloating the context window — a MemoryLake
demo driven entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
An assistant app remembers one user, Maya Lindqvist (a fictional freelance
architect): her contacts and their birthdays, her projects, her door code, what
she avoids at restaurants.  Its memory grows from 20 facts in week 1 to 100 by
month 3 and 400 by month 9, stored as facts in one MemoryLake project with one
*boundary* over it.

At each of the three points the demo asks the same four questions (two look-ups,
two "list all of them") three ways and grades every reply against what is
actually stored:

  · no memory        — the bare model (`memorylake router chat`)
  · everything       — the whole memory pasted into the system prompt, sent as a
                       provider request body (`router chat --body FILE`)
  · MemoryRouter     — the same bare request plus `--boundary`; MemoryRouter
                       attaches only the memories that matter to the question

What it shows: pasting everything grows the prompt with the memory (about 400
tokens in week 1, about 7,000 by month 9) and, at 400 facts, the model starts
mixing up neighbouring facts — a contact whose birthday is in another month
turns up in the November list.  The memory MemoryRouter attaches stays about the
same size at every point, and the lists stay exact.  A follow-up step looks every
extra name up in memory (`fact list --query`) to show what is really stored.

Model wording varies run to run; every reply is graded and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (about 40
calls on gpt-4o-mini per run).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import statistics
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

PREFIX = "mlu-ltm"  # memorylake-usecases / long-term-llm-memory

SYSTEM = "You are Maya's personal assistant. Answer briefly."
MODEL = dict(protocol="chat", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Chat Completions)")
CLAUDE = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")
MODES = [("none", "no memory"), ("everything", "everything pasted"), ("router", "MemoryRouter")]


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

def load_memory() -> dict:
    return json.loads((DATA / "memory.json").read_text(encoding="utf-8"))


def load_questions() -> list[dict]:
    return json.loads((DATA / "questions.json").read_text(encoding="utf-8"))["questions"]


def checkpoint(key: str) -> dict:
    return next(c for c in load_memory()["checkpoints"] if c["key"] == key)


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


CONTACT = re.compile(r"^Contact: (.+?) is Maya's (.+?); birthday (\w+) (\d+)\.$")


def birthdays(texts: list[str]) -> dict[str, str]:
    """name → 'Month D' for every contact fact in `texts`."""
    out = {}
    for t in texts:
        m = CONTACT.match(t)
        if m:
            out[m.group(1)] = f"{m.group(3)} {m.group(4)}"
    return out


# --------------------------------------------------------------------------- grading (deterministic)

def grade(q: dict, reply: str, stored: list[str]) -> dict:
    """Grade one reply against what is stored right now. Lists: right / missed / extra names."""
    if "expect" not in q and "month" not in q:
        return dict(ok=None, mark="")  # your own question: nothing stored says what the right answer is
    if "expect" in q:
        ok = q["expect"] in reply
        return dict(ok=ok, mark="✓" if ok else "✗")
    bdays = birthdays(stored)
    truth = sorted(n for n, d in bdays.items() if d.split()[0] == q["month"])
    named = [n for n in load_memory()["contacts"] if re.search(r"\b" + re.escape(n) + r"\b", reply)]
    right = [n for n in named if n in truth]
    missed = [n for n in truth if n not in named]
    extra = [n for n in named if n not in truth]
    ok = not missed and not extra
    mark = f"{'✓' if ok else '✗'} {len(right)}/{len(truth)}"
    if extra:
        mark += "  + " + ", ".join(extra)
    return dict(ok=ok, mark=mark, truth=truth, right=right, missed=missed, extra=extra)


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("memory"), "--by-custom-id", scoped=True, echo=echo)


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


def setup(cli: CLI) -> dict:
    old = find_project(cli, echo=True)
    if old is not None:
        n = len(list_facts(cli, old["id"], echo=False))
        note(f"an earlier run left a memory of {n} facts; starting over from week 1 with a fresh project "
             "(a new scope, so no answer can come from an earlier run's retrieval)")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Maya's assistant memory", "--custom-id", cid("memory"),
                   "--description", "Long-term memory of one user for an assistant app. Demo data.", scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: the app passes this boundary to MemoryRouter and nothing else.
    b = cli.run("boundary", "create", "--name", "Maya's assistant memory", "--custom-id", cid("boundary"),
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


def grow_to(cli: CLI, world: dict, cp: dict) -> list[str]:
    """Store the facts the assistant has learned by checkpoint `cp` (the first cp['facts'] of the timeline)."""
    want = load_memory()["facts"][:cp["facts"]]
    have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
    todo = [t for t in want if t not in have]
    batches = [todo[i:i + 50] for i in range(0, len(todo), 50)]
    if batches:
        shown = ["fact", "add", "--project", world["project"], *[textwrap.shorten(t, 50) for t in todo[:2]]]
        emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace])
             + (f"  … {len(todo) - 2} more" if len(todo) > 2 else ""))
        def add(batch: list[str]) -> bool:
            try:
                cli.run("fact", "add", "--project", world["project"], *batch, scoped=True, echo=False)
                return True
            except CLIError as e:
                if not e.has(*CLI.TRANSIENT):
                    raise
                return False

        with ThreadPoolExecutor(4) as pool:
            ok = list(pool.map(add, batches))
        if not all(ok):
            # `fact add` is not idempotent, so a dropped call is not retried blindly: add only what is still missing.
            have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
            missing = [t for t in want if t not in have]
            note(f"{ok.count(False)} `fact add` call(s) hit a network error; adding the {len(missing)} fact(s) still missing")
            for i in range(0, len(missing), 50):
                cli.run("fact", "add", "--project", world["project"], *missing[i:i + 50], scoped=True, echo=False)
        note(f"stored {len(todo)} new fact(s) in {len(batches)} `fact add` call(s)")
    facts = [f["fact"] for f in list_facts(cli, world["project"])]
    if sorted(facts) != sorted(want):
        die(f"the project holds {len(facts)} facts, expected {len(want)} for {cp['label']}")
    if todo:
        time.sleep(10)  # a new fact is in `fact list` at once; retrieval takes a few seconds to see it
    emit("memory", "", checkpoint=cp["key"], label=cp["label"], facts=len(facts))
    return facts


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, boundary: str | None = None, body: Path | None = None, tool: dict = MODEL,
           max_tokens: int = 300, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. Returns {reply, prompt_tokens, seconds} or None if unavailable."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["router", tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", str(max_tokens),
            "--temperature", "0"]
    if body:
        args += ["--body", str(body.relative_to(HERE))]   # the system prompt with the whole memory is in the body
    else:
        args += ["--system", SYSTEM]
    if boundary:
        args += ["--boundary", boundary]
    t0 = time.time()
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


def everything_body(facts: list[str], name: str) -> Path:
    """The usual way to give a model long-term memory: paste all of it into the system prompt."""
    OUT.mkdir(exist_ok=True)
    path = OUT / f"everything-{name}.json"
    system = SYSTEM + "\n\nEverything you know about Maya:\n" + "\n".join("- " + f for f in facts)
    path.write_text(json.dumps({"model": MODEL["model"], "messages": [{"role": "system", "content": system}]},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def ask_modes(cli: CLI, world: dict, q: dict, facts: list[str], body: Path, modes=("none", "everything", "router"),
              echo: bool = False, on_answer=None) -> dict:
    res = {}
    for m in modes:  # "none" first: the other two are measured against it
        r = router(cli, q["prompt"], boundary=world["boundary"] if m == "router" else None,
                   body=body if m == "everything" else None, echo=echo)
        if r is None:
            return {}
        r.update(grade(q, r["reply"], facts))
        r["memory_tokens"] = r["prompt_tokens"] - res["none"]["prompt_tokens"] if m != "none" and "none" in res else 0
        res[m] = r
        if on_answer:
            on_answer(q, m, r)
    return res


# --------------------------------------------------------------------------- one checkpoint: 4 questions × 3 ways

def ask_round(cli: CLI, world: dict, cp: dict, facts: list[str]) -> dict:
    qs = load_questions()
    body = everything_body(facts, cp["key"])
    size = len(json.loads(body.read_text(encoding="utf-8"))["messages"][0]["content"])
    q0 = qs[0]["prompt"]
    for m, _ in MODES:  # echo one command per way of asking; the other questions are the same shape
        args = ["router", "chat", q0, "--model", MODEL["model"], "--max-tokens", "300", "--temperature", "0"]
        args += ["--body", str(body.relative_to(HERE))] if m == "everything" else ["--system", SYSTEM]
        args += ["--boundary", world["boundary"]] if m == "router" else []
        cli._echo(args)
    note(f"{body.relative_to(HERE)} holds the whole memory in its system message ({size:,} characters)")
    note(f"{len(qs) * len(MODES)} calls: {len(qs)} questions × {len(MODES)} ways, 4 at a time "
         "(one command per way is echoed)")
    labels = dict(MODES)

    def on_answer(q: dict, m: str, x: dict) -> None:  # the browser fills its table as replies arrive
        emit("answer", "", checkpoint=cp["key"], question=q["key"], question_label=q["label"], mode=m,
             mode_label=labels[m], reply=x["reply"], memory_tokens=x["memory_tokens"], prompt_tokens=x["prompt_tokens"],
             seconds=x["seconds"], ok=x["ok"], mark=x["mark"], extra=x.get("extra") or [],
             missed=x.get("missed") or [], truth=x.get("truth"))

    with ThreadPoolExecutor(4) as pool:
        res = list(pool.map(lambda q: ask_modes(cli, world, q, facts, body, on_answer=on_answer), qs))
    if any(not r for r in res):
        return {}
    lines = [f"\n    {'question':<20}{'asked with':<19}{'memory in prompt':>17}{'time':>7}   answer"]
    rows = []
    for q, r in zip(qs, res):
        for i, (m, label) in enumerate(MODES):
            x = r[m]
            mem = "—" if m == "none" else f"{x['memory_tokens']:,} tok"
            lines.append(f"    {q['label'] if i == 0 else '':<20}{label:<19}{mem:>17}{x['seconds']:>6.1f}s   {x['mark']}")
            rows.append(dict(question=q["key"], label=q["label"], mode=m, **{k: v for k, v in x.items()}))
    emit("text", "\n".join(lines))
    ev = [r for r in rows if r["mode"] == "everything"]
    ro = [r for r in rows if r["mode"] == "router"]
    summary = dict(
        checkpoint=cp["key"], label=cp["label"], facts=len(facts),
        everything_tokens=int(statistics.mean(r["memory_tokens"] for r in ev)),
        router_tokens=[min(r["memory_tokens"] for r in ro), max(r["memory_tokens"] for r in ro)],
        exact={m: sum(1 for r in rows if r["mode"] == m and r["ok"]) for m, _ in MODES},
        extra={m: sorted({n for r in rows if r["mode"] == m for n in r.get("extra") or []}) for m, _ in MODES},
        seconds={m: round(statistics.median(r["seconds"] for r in rows if r["mode"] == m), 1) for m, _ in MODES},
        rows=rows)
    note(f"memory in prompt = prompt tokens minus the same question with no memory. Pasted: "
         f"{summary['everything_tokens']:,} tokens on every question. MemoryRouter attached "
         f"{summary['router_tokens'][0]}–{summary['router_tokens'][1]} tokens.")
    emit("round", "", **{k: v for k, v in summary.items() if k != "rows"})
    return summary


# --------------------------------------------------------------------------- step 6: where did the extra names come from?

def explain_extras(cli: CLI, world: dict, rounds: list[dict]) -> list[dict]:
    found = []
    for rd in rounds:
        for r in rd["rows"]:
            for n in r.get("extra") or []:
                if n not in [f["name"] for f in found]:
                    found.append(dict(name=n, checkpoint=rd["label"], mode=r["mode"], question=r["label"], reply=r["reply"]))
    if not found:
        note("no reply named anyone who does not belong in its list this run; the README gives the totals over every run")
        return []
    out = []
    for f in found[:4]:
        said = next((ln.strip(" -*") for ln in f["reply"].splitlines() if f["name"] in ln), f["name"])
        emit("text", f"\n  {f['checkpoint']} · {dict(MODES)[f['mode']]} · {f['question']}: the reply said “{said}”")
        hits = (cli.run("fact", "list", "--projects", world["project"], "--query", f["name"], "--page-size", "50",
                        scoped=True) or {}).get("items") or []
        contact = next((h["fact"] for h in hits if h["fact"].startswith("Contact:")), None)
        for h in sorted(hits, key=lambda h: not h["fact"].startswith("Contact:"))[:3]:
            emit("text", f"      memory: {textwrap.shorten(h['fact'], 96)}")
        if len(hits) > 3:
            emit("text", f"      … {len(hits) - 3} more fact(s) mention {f['name']}")
        stored = birthdays([contact]).get(f["name"]) if contact else None
        note(f"stored birthday: {stored}" if stored else "no contact of that name is stored now")
        out.append(dict(name=f["name"], said=said, stored=stored, mode=f["mode"], checkpoint=f["checkpoint"],
                        question=f["question"], facts=[h["fact"] for h in hits]))
        emit("extra", "", **out[-1])
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    mem = load_memory()
    banner(2, TOTAL, "Set up — one memory project and one boundary the app passes to MemoryRouter")
    world = setup(cli)
    emit("text", f"\n  The user: {mem['user']}. Her assistant's memory grows over nine months:\n"
         + "\n".join(f"   - {c['label']}: {c['facts']} facts" for c in mem["checkpoints"])
         + "\n  Four questions at every point; each reply is graded against what is stored at that moment.")

    rounds = []
    for i, cp in enumerate(mem["checkpoints"]):
        banner(3 + i, TOTAL, f"{cp['label']} — {cp['facts']} facts in memory; the same four questions, three ways")
        facts = grow_to(cli, world, cp)
        if i == 0:
            emit("text", "\n  A few of them:\n" + "\n".join(f"   - {textwrap.shorten(f, 100)}" for f in facts[:4] + facts[6:8]))
        rd = ask_round(cli, world, cp, facts)
        if not rd:
            break
        rounds.append(rd)

    banner(6, TOTAL, "Where the extra names came from — each one looked up in memory")
    extras = explain_extras(cli, world, rounds) if rounds else []
    if extras:
        note("the memory holds the right date; a prompt with every fact in it put the name next to the wrong month")

    banner(7, TOTAL, "The scoreboard — and the same boundary from another model")
    lines = [f"    {'memory':<18}{'pasted, tokens':>15}{'MemoryRouter, tokens':>22}{'exact answers':>21}{'extra names':>19}",
             f"    {'':<18}{'per question':>15}{'per question':>22}{'pasted · router':>21}{'pasted · router':>19}"]
    for rd in rounds:
        lo, hi = rd["router_tokens"]
        lines.append(f"    {rd['label'] + ' (' + str(rd['facts']) + ')':<18}{rd['everything_tokens']:>15,}"
                     f"{(str(lo) + '–' + str(hi)):>22}"
                     f"{str(rd['exact']['everything']) + '/4 · ' + str(rd['exact']['router']) + '/4':>21}"
                     f"{str(len(rd['extra']['everything'])) + ' · ' + str(len(rd['extra']['router'])):>19}")
    emit("text", "\n".join(lines))
    if rounds:
        med = {m: statistics.median(r["seconds"] for rd in rounds for r in rd["rows"] if r["mode"] == m) for m, _ in MODES}
        note("median seconds per call (CLI round trip included): "
             + " · ".join(f"{label} {med[m]:.1f}s" for m, label in MODES))
    emit("scoreboard", "", rows=[{k: v for k, v in rd.items() if k != "rows"} for rd in rounds])

    other = None
    if cli.router_ok and rounds:  # type: ignore[attr-defined]
        q = next(x for x in load_questions() if x["key"] == "november")
        emit("text", f"\n  Same boundary, different model and API: {CLAUDE['label']}")
        r = router(cli, q["prompt"], boundary=world["boundary"], tool=CLAUDE)
        if r:
            r.update(grade(q, r["reply"], facts))
            emit("text", "\n".join("      │ " + ln for ln in r["reply"].splitlines() if ln.strip()))
            note(f"{q['label']}: {r['mark']}")
            other = r
            emit("other_model", "", model=CLAUDE["label"], question=q["label"], reply=r["reply"], mark=r["mark"], ok=r["ok"])

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rounds=rounds, extras=extras, other_model=other, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every reply and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def find_question(text: str) -> dict:
    for q in load_questions():
        if text.strip().lower() in (q["key"], q["prompt"].lower()):
            return q
    return dict(key="custom", label="Your question", prompt=text)


def ask(cli: CLI, text: str, mode: str = "all") -> dict:
    world = load_world(cli)
    facts = [f["fact"] for f in list_facts(cli, world["project"], echo=False)]
    q = find_question(text)
    body = everything_body(facts, "now")
    modes = [m for m, _ in MODES] if mode == "all" else ["none", mode] if mode != "none" else ["none"]
    res = ask_modes(cli, world, q, facts, body, modes=modes, echo=True)
    if not res:
        return {}
    for m, label in MODES:
        if m not in res or (mode not in ("all", "none") and m == "none"):
            continue
        x = res[m]
        graded = q["key"] != "custom"
        head = f"\n    {label} · memory in prompt {'—' if m == 'none' else format(x['memory_tokens'], ',') + ' tok'}" \
               f" · {x['seconds']:.1f}s" + (f" · {x['mark']}" if graded else "")
        emit("text", head + "\n" + "\n".join("      │ " + ln for ln in x["reply"].splitlines() if ln.strip()))
    return dict(question=q, memory=len(facts), results=res)


def memory(cli: CLI) -> None:
    world = load_world(cli)
    facts = [f["fact"] for f in list_facts(cli, world["project"])]
    b = birthdays(facts)
    emit("text", f"\n  boundary {world['boundary']} → project {world['project']}: {len(facts)} fact(s), "
                 f"{len(b)} contacts with a birthday")
    for q in load_questions():
        if "month" in q:
            names = sorted(n for n, d in b.items() if d.split()[0] == q["month"])
            emit("text", f"   {q['label']}: " + ", ".join(f"{n} ({b[n]})" for n in names))


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
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: delete_fact(cli, f["id"], proj["id"]), facts))
        left = list_facts(cli, proj["id"], echo=False)
        if left:
            die(f"{len(left)} fact(s) are still in the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted {len(facts)} fact(s) and the memory project")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "memory", "cleanup"],
                    help="run = full demo (default); ask = one question, three ways, against the memory as it is now; "
                         "memory = what is stored; cleanup = delete everything")
    ap.add_argument("question", nargs="?", help="for ask: door, passport, november, march, or your own question in quotes")
    ap.add_argument("--mode", default="all", choices=["all", "none", "everything", "router"], help="for ask")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over from week 1")
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
        banner(2, 2, "What the assistant remembers")
        memory(cli)
        return
    if args.command == "ask":
        if not args.question:
            die('usage: python3 demo.py ask door|passport|november|march|"your own question"')
        banner(2, 2, "One question, asked against the memory as it is now")
        ask(cli, args.question, args.mode)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py ask "Who are my climbing partners?"`; '
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

#!/usr/bin/env python3
"""
Training Q&A memory: the policy changed, the answers it shaped did not — a
MemoryLake demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur (fictional) keeps its support training memory in one MemoryLake
project, with one *boundary* over it: 20 procedures, 5 policies, and the
questions the spring cohort asked with the answers a senior agent, Maya Chen,
gave them.  New agents ask a training assistant that reads this memory through
MemoryRouter.

On 2026-10-14 the team lead tightened five policies (refund window 30 → 14
days, dispute escalation $500 → $200, …) and updated the policy facts.  On
2026-10-15 Jun, a new agent, asks five questions — the same situations the
spring cohort asked about.

What it shows
-------------
  · The policy facts are current, but a worked answer to the same situation
    is a closer match to Jun's question than the rule it came from: MemoryRouter
    attaches the spring cohort's Q&A, usually without the current policy, and
    the model repeats it: "Yes, approve it: annual plans are fully refundable
    for 30 days."
  · Why: a checker (Claude, told to copy out everything above the request)
    shows what was attached to each question.  Where the policy and the old
    answer were attached side by side, the model still went with the answer.
  · The usual fix, a change note on the policy ("changed on 2026-10-14; before
    that it was 30 days"), helps on some questions, not reliably.
  · The fix that holds is in how the Q&A is stored: each answer records which
    policy it relies on (`metadata.relies_on`), so a policy change finds the
    answers it invalidated and they get re-answered.

Model wording varies run to run; every reply is graded and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (15 calls
on gpt-4o-mini and 5 on claude-haiku-4-5 per run).
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

PREFIX = "mlu-tqm"  # memorylake-usecases / training-qa-memory

SYSTEM = ("You are Larkspur's training assistant for new support agents. Start your answer with Yes or No, then "
          "one short sentence, using the team's current policies.")


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

STATES = {
    "updated": dict(label="Policies updated", tag="the policy facts say the new rules; the spring Q&A is as it was"),
    "noted": dict(label="+ change note", tag="each changed policy also says what it replaced and when"),
    "swept": dict(label="Q&A re-answered", tag="the answers that relied on a changed policy were found and re-answered"),
}


# --------------------------------------------------------------------------- data

def load_training() -> dict:
    return json.loads((DATA / "training.json").read_text(encoding="utf-8"))


def qa_text(tr: dict, qa: dict, reanswered: bool = False) -> str:
    c = tr["cohort"]
    who = f"{c['name']}, asked by trainee {qa['asked_by']}, answered by {c['senior']}"
    if reanswered:
        ch = tr["policy_change"]
        who += f"; re-answered on {ch['on']} by {ch['by']} after the policy change"
        return f"Cohort Q&A ({who}): '{qa['q']}' {qa['a2']}"
    return f"Cohort Q&A ({who}): '{qa['q']}' {qa['a']}"


def memory_entries(tr: dict) -> list[dict]:
    """What the training memory holds before the policy change: (text, metadata) per fact."""
    out = [dict(text=t, meta=dict(kind="procedure", key=f"proc-{i + 1}")) for i, t in enumerate(tr["procedures"])]
    out += [dict(text=p["v1"], meta=dict(kind="policy", key=p["key"], version=1)) for p in tr["policies"]]
    out += [dict(text=qa_text(tr, q), meta=dict(kind="qa", key=q["key"], cohort=tr["cohort"]["name"],
                                                 asked_by=q["asked_by"], relies_on=q["relies_on"]))
            for q in tr["qa"]]
    return out


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def verdict(reply: str) -> str:
    m = re.match(r"\W*(yes|no)\b", reply, re.I)
    return m.group(1).lower() if m else "?"


def follows(policy: dict, reply: str) -> str:
    """Which rule the reply quotes: the new one, the old one, or neither / both."""
    new, old = policy["new"].lower() in reply.lower(), policy["old"].lower() in reply.lower()
    if new and not old:
        return "new"
    if old and not new:
        return "old"
    return ""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("training"), "--by-custom-id", scoped=True, echo=echo)


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
        note(f"an earlier run left a training memory of {n} facts; starting over with a fresh project "
             "(the story begins before the policy change)")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Larkspur support training", "--custom-id", cid("training"),
                   "--description", "Procedures, policies and cohort Q&A for new support agents. Demo data.",
                   scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: the training assistant passes this boundary to MemoryRouter and nothing else.
    b = cli.run("boundary", "create", "--name", "Larkspur training assistant", "--custom-id", cid("boundary"),
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


# --------------------------------------------------------------------------- step 3: the training memory

def set_meta(cli: CLI, fact_id: str, project: str, meta: dict, text: str | None = None, echo: bool = False) -> None:
    """`fact update` is idempotent (same text / metadata set again), so a dropped connection is retried."""
    args = ["fact", "update", fact_id, "--project", project]
    if text is not None:
        args += ["--text", text]
    args += ["--metadata", json.dumps(meta, ensure_ascii=False)]
    for attempt in range(5):
        try:
            cli.run(*args, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def store(cli: CLI, world: dict) -> dict[str, dict]:
    tr = load_training()
    entries = memory_entries(tr)
    texts = [e["text"] for e in entries]
    shown = ["fact", "add", "--project", world["project"], *texts[:2]]
    emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace]) + f"  … {len(texts) - 2} more")
    try:
        res = cli.run("fact", "add", "--project", world["project"], *texts, scoped=True, echo=False)
        ids = [f["id"] for f in res["facts"]]
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        # `fact add` is not idempotent: add only what is still missing, then map texts to ids from the list.
        have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
        missing = [t for t in texts if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", "--project", world["project"], *missing, scoped=True, echo=False)
        by_text = {f["fact"]: f["id"] for f in list_facts(cli, world["project"], echo=False)}
        ids = [by_text[t] for t in texts]
    first = entries[0]
    emit("cmd", shlex.join(["memorylake", "fact", "update", ids[0], "--project", world["project"], "--metadata",
                            json.dumps(first["meta"]), "--workspace", cli.workspace]) + f"  … one per fact")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda pair: set_meta(cli, pair[0], world["project"], pair[1]["meta"]), zip(ids, entries)))
    facts = list_facts(cli, world["project"])
    if sorted(f["fact"] for f in facts) != sorted(texts):
        die(f"the project holds {len(facts)} facts, expected {len(texts)}")
    kinds = {k: sum(1 for e in entries if e["meta"]["kind"] == k) for k in ("procedure", "policy", "qa")}
    note(f"stored {len(facts)} facts: {kinds['procedure']} procedures, {kinds['policy']} policies, "
         f"{kinds['qa']} Q&A from the {tr['cohort']['name']}; each carries metadata (kind, key, and for Q&A "
         f"`relies_on`: the policies the answer depends on)")
    by_key = {e["meta"]["key"]: dict(id=i, **e) for i, e in zip(ids, entries)}
    pol = tr["policies"][0]
    qa = tr["qa"][0]
    emit("text", "\n  Two of them, as the spring cohort learned them:\n"
         f"   - policy:  {pol['v1']}\n"
         f"   - Q&A:     {qa_text(tr, qa)}\n"
         f"              metadata.relies_on = {json.dumps(qa['relies_on'])}")
    emit("memory", "", facts=len(facts), kinds=kinds)
    return by_key


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, tool: dict = MODEL,
           max_tokens: int = 200, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. `body` holds the system message (and any conversation so far)."""
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
            note("model replies below are skipped; the memory steps still run. Top up in the console to see the replies.")
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


def write_body(name: str, system: str, tool: dict = MODEL) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    if tool["protocol"] == "chat":
        body = {"model": tool["model"], "messages": [{"role": "system", "content": system}]}
    else:
        body = {"model": tool["model"], "system": system, "messages": []}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def ask_all(cli: CLI, world: dict, state: str, quiet: bool = False) -> list[dict]:
    """Jun's five questions, each a fresh one-question chat through MemoryRouter with the training boundary."""
    tr = load_training()
    pols = {p["key"]: p for p in tr["policies"]}
    body = write_body("ask", SYSTEM)
    rows = []
    for n, q in enumerate(tr["questions"], 1):
        r = router(cli, q["say"], body, boundary=world["boundary"], echo=not quiet and n == 1)
        reply = r["reply"] if r else ""
        v = verdict(reply) if r else None
        ok = None if r is None else v == q["expect"]
        row = dict(state=state, key=q["key"], say=q["say"], expect=q["expect"], reply=reply, verdict=v, ok=ok,
                   follows=follows(pols[q["key"]], reply) if r else "")
        rows.append(row)
        if not quiet:
            show_answer(row, pols[q["key"]])
        emit("answer", "", **row)
        if n == 1 and not quiet and r:
            note("the other questions are sent the same way, each as a new chat")
    got = [r for r in rows if r["ok"] is not None]
    if got and not quiet:
        old = sum(1 for r in got if r["follows"] == "old")
        emit("text", f"\n    {sum(r['ok'] for r in got)}/{len(got)} answers right"
             + (f" · {old} quote the rule the spring cohort was taught" if old else ""))
    return rows


def show_answer(row: dict, policy: dict) -> None:
    lines = [f"\n    Jun: {row['say']}"]
    if row["ok"] is not None:
        tail = {"old": f"   ← the old rule ({policy['old']})", "new": ""}.get(row["follows"], "")
        lines.append(f"    {mark(row['ok'])} {textwrap.shorten(row['reply'], 100)}{tail}")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 6: what MemoryRouter attached

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). Then answer the question.")


def attached(cli: CLI, world: dict, say: str) -> dict | None:
    """Ask Claude the same question through the same boundary, told (in the system prompt) to copy out first every
    memory MemoryRouter attached. The user message is unchanged, so retrieval sees the same query."""
    body = write_body("checker", CHECK_SYSTEM, tool=CHECKER)
    r = router(cli, say, body, boundary=world["boundary"], tool=CHECKER, max_tokens=1500, echo=False)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    return dict(lines=[ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")])


def classify(line: str, tr: dict, key: str) -> str:
    """Label a copied-out memory line against what the demo stored (word for word, ignoring case and spacing)."""
    n = norm(line)
    for p in tr["policies"]:
        if n in (norm(p["v1"]), norm(p["v2"]), norm(p["noted"])):
            if p["key"] != key:
                return "other policy"
            return "policy (OLD)" if n == norm(p["v1"]) else "policy (current)"
    for q in tr["qa"]:
        if n == norm(qa_text(tr, q)):
            return "spring Q&A (old answer)" if key in q["relies_on"] else "other Q&A"
        if q.get("a2") and n == norm(qa_text(tr, q, reanswered=True)):
            return "Q&A (re-answered)" if key in q["relies_on"] else "other Q&A"
    if n in {norm(t) for t in tr["procedures"]}:
        return "procedure"
    return "? not a stored fact"


def inspect_all(cli: CLI, world: dict, answers: list[dict]) -> list[dict]:
    tr = load_training()
    if not cli.router_ok:  # type: ignore[attr-defined]
        note("skipped: needs MemoryRouter")
        return []
    checks = []
    for q, ans in zip(tr["questions"], answers):
        c = attached(cli, world, q["say"])
        if c is None:
            break
        labelled = [(classify(ln, tr, q["key"]), ln) for ln in c["lines"]]
        labs = [lab for lab, _ in labelled]
        row = dict(key=q["key"], say=q["say"], lines=c["lines"], labels=labs, ok=ans["ok"],
                   policy="policy (current)" in labs, old_qa="spring Q&A (old answer)" in labs)
        checks.append(row)
        out = [f"\n    Jun: {q['say']}", f"      MemoryRouter attached {len(c['lines'])} line(s):"]
        for lab, ln in labelled:
            out.append(f"        {('[' + lab + ']') if lab else '':<26}{textwrap.shorten(ln, 92)}")
        if not row["policy"]:
            out.append("        (the current policy is not among them)")
        emit("text", "\n".join(out))
        emit("inspect", "", **row)
    if checks:
        note(f"the spring Q&A was attached on {sum(c['old_qa'] for c in checks)} of {len(checks)} questions; "
             f"the current policy on {sum(c['policy'] for c in checks)}. A worked answer to the same situation is the "
             "closest match to Jun's question, closer than the rule it was derived from")
        note("the checker is its own call, so what it was given can differ a little from what the answer above got; "
             "over many runs the pattern holds (README, Measured)")
    return checks


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def change_policies(cli: CLI, world: dict, by_key: dict) -> None:
    tr = load_training()
    ch = tr["policy_change"]
    for p in tr["policies"]:
        f = by_key[p["key"]]
        meta = dict(kind="policy", key=p["key"], version=2, changed_on=ch["on"], changed_by=ch["by"], previous=p["v1"])
        set_meta(cli, f["id"], world["project"], meta, text=p["v2"], echo=True)
        f.update(text=p["v2"], meta=meta)
        emit("text", f"      {p['label']}: {p['old']} → {p['new']}")
        emit("policy", "", key=p["key"], label=p["label"], old=p["old"], new=p["new"])


def note_changes(cli: CLI, world: dict, by_key: dict) -> None:
    tr = load_training()
    for p in tr["policies"]:
        f = by_key[p["key"]]
        meta = dict(f["meta"], noted=True)
        set_meta(cli, f["id"], world["project"], meta, text=p["noted"], echo=p is tr["policies"][0])
        f.update(text=p["noted"], meta=meta)
    emit("text", "\n  Each changed policy now reads, for example:\n    " + tr["policies"][0]["noted"])


def sweep(cli: CLI, world: dict) -> list[dict]:
    """Find every Q&A whose `relies_on` names a changed policy, and re-answer it. Q&A that rely on nothing that
    changed are left alone."""
    tr = load_training()
    facts = list_facts(cli, world["project"])
    changed = {(f.get("metadata") or {}).get("key"): f for f in facts
               if (f.get("metadata") or {}).get("kind") == "policy" and (f.get("metadata") or {}).get("version", 1) > 1}
    qas = [f for f in facts if (f.get("metadata") or {}).get("kind") == "qa"]
    hit = [f for f in qas if set((f.get("metadata") or {}).get("relies_on") or []) & set(changed)]
    note(f"{len(changed)} policies changed on {tr['policy_change']['on']} ({', '.join(sorted(changed))}); "
         f"{len(hit)} of {len(qas)} Q&A rely on one of them")
    by_qkey = {q["key"]: q for q in tr["qa"]}
    done = []
    for f in sorted(hit, key=lambda f: f["metadata"]["key"]):
        m = f["metadata"]
        q = by_qkey[m["key"]]
        pol = changed[m["relies_on"][0]]
        new_text = qa_text(tr, q, reanswered=True)
        emit("text", f"\n    relies on “{m['relies_on'][0]}” (changed {pol['metadata'].get('changed_on')}):\n"
             f"      was: {textwrap.shorten(q['a'], 90)}\n"
             f"      now: {textwrap.shorten(q['a2'], 90)}")
        meta = dict(m, reanswered_on=tr["policy_change"]["on"], previous=f["fact"])
        set_meta(cli, f["id"], world["project"], meta, text=new_text, echo=not done)
        row = dict(key=m["key"], relies_on=m["relies_on"], was=q["a"], now=q["a2"], q=q["q"])
        done.append(row)
        emit("reanswer", "", **row)
    note(f"left alone: {len(qas) - len(hit)} Q&A that rely on nothing that changed")
    return done


def scoreboard(runs: dict[str, list[dict]]) -> dict:
    tr = load_training()
    lines = [f"\n    {'Jun asks':<52}{'updated':>9}{'+ note':>8}{'swept':>8}"]
    rows = []
    for i, q in enumerate(tr["questions"]):
        cells = {s: runs[s][i]["ok"] for s in runs}
        rows.append(dict(key=q["key"], say=q["say"], **{s: cells.get(s) for s in STATES}))
        lines.append(f"    {textwrap.shorten(q['say'], 50):<52}{mark(cells.get('updated')):>9}"
                     f"{mark(cells.get('noted')):>8}{mark(cells.get('swept')):>8}")
    tot = {s: sum(1 for r in runs[s] if r["ok"]) for s in runs}
    lines.append(f"    {'':<52}{tot.get('updated', 0):>7}/5{tot.get('noted', 0):>6}/5{tot.get('swept', 0):>6}/5")
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=rows, totals=tot)
    return tot


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    tr = load_training()
    banner(2, TOTAL, "Set up — one training-memory project and one boundary the assistant passes to MemoryRouter")
    world = setup(cli)

    banner(3, TOTAL, "The training memory — procedures, policies, and the spring cohort's Q&A")
    by_key = store(cli, world)

    banner(4, TOTAL, f"{tr['policy_change']['on']} — the team lead tightens five policies and updates the policy facts")
    change_policies(cli, world, by_key)
    note("the Q&A are untouched: nobody went back to the answers the spring cohort was given")
    time.sleep(10)  # retrieval sees an update a few seconds after it lands

    runs: dict[str, list[dict]] = {}
    banner(5, TOTAL, f"{tr['today']} — Jun, a new agent, asks the training assistant five questions")
    note("each question goes to `router chat --boundary` (gpt-4o-mini, temperature 0); every answer is in memory")
    runs["updated"] = ask_all(cli, world, "updated")

    banner(6, TOTAL, "Why — what MemoryRouter attached (Claude copies out what sits above the question)")
    note("the same five questions through the same boundary to claude-haiku-4-5, told to copy out its memory first")
    checks = inspect_all(cli, world, runs["updated"])

    banner(7, TOTAL, "The usual fix — say on each policy what changed and when")
    note_changes(cli, world, by_key)
    time.sleep(10)
    runs["noted"] = ask_all(cli, world, "noted")

    banner(8, TOTAL, "The fix in the memory — find the answers that relied on a changed policy, and re-answer them")
    note("`fact list` with metadata: a Q&A whose `relies_on` names a policy at version 2 was answered under the old rule")
    swept = sweep(cli, world)
    time.sleep(10)

    banner(9, TOTAL, "Jun asks again — and the scoreboard")
    runs["swept"] = ask_all(cli, world, "swept")
    tot = scoreboard(runs)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(runs=runs, checks=checks, swept=swept, totals=tot, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every question, answer and attached line: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def ask_one(cli: CLI, say: str) -> dict:
    """One question through MemoryRouter with the training boundary, as the memory stands now."""
    world = load_world(cli)
    r = router(cli, say, write_body("ask-one", SYSTEM), boundary=world["boundary"])
    if not r:
        die("MemoryRouter is not available on this account (no balance)")
    return dict(say=say, reply=r["reply"], seconds=r["seconds"])


def show_qa(cli: CLI) -> list[dict]:
    """The cohort Q&A as stored now: what each relies on, and whether it was re-answered after a policy change."""
    world = load_world(cli)
    facts = list_facts(cli, world["project"])
    pols = {(f.get("metadata") or {}).get("key"): f for f in facts if (f.get("metadata") or {}).get("kind") == "policy"}
    rows = []
    for f in sorted((f for f in facts if (f.get("metadata") or {}).get("kind") == "qa"),
                    key=lambda f: (not f["metadata"].get("relies_on"), f["metadata"]["key"])):
        m = f["metadata"]
        dep = m.get("relies_on") or []
        state = (f"re-answered {m['reanswered_on']}" if m.get("reanswered_on")
                 else ("relies on a CHANGED policy" if any((pols.get(k) or {}).get("metadata", {}).get("version", 1) > 1
                                                          for k in dep) else "current"))
        a = f["fact"].split("' ", 1)[-1]
        emit("text", f"  {m['key']:<14}{('relies on ' + ', '.join(dep)) if dep else 'no policy':<20}{state:<26}"
                     f"{textwrap.shorten(a, 60)}")
        rows.append(dict(key=m["key"], relies_on=dep, state=state, answer=a))
    return rows


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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "qa", "cleanup"],
                    help="run = full demo (default); ask \"<question>\" = one question through MemoryRouter as the "
                         "memory stands now; qa = the cohort Q&A and what each relies on; cleanup = delete everything")
    ap.add_argument("question", nargs="?", help="for ask: the question")
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
    if args.command == "qa":
        banner(2, 2, "The cohort Q&A — what each answer relies on")
        show_qa(cli)
        return
    if args.command == "ask":
        if not args.question:
            die('usage: python3 demo.py ask "<question>"')
        banner(2, 2, "One question to the training assistant, as the memory stands now")
        r = ask_one(cli, args.question)
        emit("text", f"\n    {r['reply']}")
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py qa` or `python3 demo.py ask "Can I extend a trial by 2 days?"`; '
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

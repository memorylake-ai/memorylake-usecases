#!/usr/bin/env python3
"""agent-eval-memory — evaluate an agent against pinned memory, so a grade change means something.

Helio (a fictional invoicing SaaS) runs a support assistant on MemoryRouter: 60 support facts in one project,
one boundary, every call is an ordinary model call with `--boundary` added. The team keeps an 8-question eval
set and wants to know whether prompt v2 is better than prompt v1.

The obvious eval runs the set through the assistant as it is wired in production. Two problems show up:
  · the memory MemoryRouter attaches is picked per call. Ask the same question twice, with nothing changed, and
    the attached memory can differ — `usage.prompt_tokens` is the only trace of it; the reply never says which
    memories were used. So grades flip between identical runs (an A/A test), and a v1 → v2 difference is
    partly memory noise;
  · when an answer is wrong, nothing tells you whether the memory or the model failed.

The fix the use-case page names — evaluation against pinned memory snapshots: retrieve once per question
(`search --types fact --top-k 8`), save the fact ids and texts as the snapshot, and run every prompt version
against that same snapshot (`router chat --body`, the memory in the system prompt, no boundary). The memory
is now identical across calls (the prompt tokens stop moving), and every failure can be classified: the fact
the grader needs is missing from the snapshot (a retrieval failure) or it is there and the answer left it out
(a generation failure).

Grades are deterministic: each question lists the facts a right answer must state (a pattern each) and the
mistakes it must not make. Model wording and what MemoryRouter attaches vary run to run; the README reports
totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.  Router
calls are billed to your MemoryLake balance (80 calls on gpt-4o-mini per run, about 16,000 tokens).
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

PREFIX = "mlu-aev"  # memorylake-usecases / agent-eval-memory


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
PROMPTS = ("v1", "v2")
AA_RUNS = 3  # v1 runs per question, through MemoryRouter and against the snapshot
SNAPSHOT_K = 8


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "helio.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def key_of(text: str, d: dict) -> str:
    n = norm(text)
    for f in d["kb"]:
        if norm(f["text"]) == n:
            return f["key"]
    return "?"


def text_of(key: str, d: dict) -> str:
    return next(f["text"] for f in d["kb"] if f["key"] == key)


def pick_question(d: dict, qid: str) -> dict:
    for q in d["questions"]:
        if q["id"].lower() == qid.lower():
            return q
    die(f"unknown question {qid!r}; one of: {', '.join(q['id'] for q in d['questions'])}")


def grade(reply: str, q: dict) -> dict:
    """Right = every required statement is there and none of the listed mistakes is."""
    missing = [m for m in q["must"] if not re.search(m["re"], reply or "", re.I)]
    wrong = [f for f in q["forbid"] if re.search(f["re"], reply or "", re.I)]
    return dict(ok=not missing and not wrong, missing=[m["label"] for m in missing],
                missing_facts=sorted({k for m in missing for k in m["facts"]}), wrong=[f["label"] for f in wrong])


def marks(rows: list[dict]) -> str:
    return " ".join("·" if r["ok"] is None else ("✓" if r["ok"] else "✗") for r in rows)


def toks(rows: list[dict]) -> str:
    return " · ".join(str(r["prompt_tokens"]) if r["prompt_tokens"] else "–" for r in rows)


def flipped(rows: list[dict]) -> bool:
    return len({r["ok"] for r in rows if r["ok"] is not None}) > 1


def moved(rows: list[dict]) -> bool:
    return len({r["prompt_tokens"] for r in rows if r["prompt_tokens"]}) > 1


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("kb"), "--by-custom-id", scoped=True, echo=echo)


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
        note("an earlier run left Helio's memory behind; starting over")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Helio support knowledge", "--custom-id", cid("kb"),
                   "--description", "What Helio's support assistant knows. Demo data.", scoped=True)
    b = cli.run("boundary", "create", "--name", "Helio support assistant", "--custom-id", cid("assistant"),
                "--projects", proj["id"], scoped=True)
    note(f"the support assistant's boundary {b['id']} covers the project: every production call is "
         "`router chat … --boundary` and MemoryRouter picks what to attach")
    world = dict(project=proj["id"], boundary=b["id"])
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    proj = find_project(cli)
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    if not proj or cid("assistant") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], boundary=bs[cid("assistant")])


# --------------------------------------------------------------------------- step 3: the memories

def add_facts(cli: CLI, texts: list[str], project: str, echo: bool = True) -> None:
    """`fact add` with many texts in one call. Not idempotent: after a network error, add only what is missing."""
    if echo:
        more = f"  … {len(texts) - 2} more" if len(texts) > 2 else ""
        emit("cmd", shlex.join(["memorylake", "fact", "add", "--project", project, *texts[:2],
                                "--workspace", cli.workspace]) + more)
    try:
        cli.run("fact", "add", "--project", project, *texts, scoped=True, echo=False)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        have = {f["fact"] for f in list_facts(cli, project, echo=False)}
        missing = [t for t in texts if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", "--project", project, *missing, scoped=True, echo=False)


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    texts = [f["text"] for f in d["kb"]]
    for k in range(0, len(texts), 30):
        add_facts(cli, texts[k:k + 30], world["project"], echo=k == 0)
    facts = list_facts(cli, world["project"])
    if sorted(f["fact"] for f in facts) != sorted(texts):
        die(f"the project holds {len(facts)} facts, expected {len(texts)}")
    topics: dict[str, int] = {}
    for f in d["kb"]:
        topics[f["topic"]] = topics.get(f["topic"], 0) + 1
    note(f"{len(facts)} support facts, {len(topics)} topics × {len(facts) // len(topics)}: " + ", ".join(topics))
    emit("text", "\n  A few of them:\n" + "\n".join(f"    - {text_of(k, d)}"
                                                  for k in ("refunds-1", "refunds-3", "sso-1", "webhooks-1")))
    emit("memory", "", count=len(facts), topics=topics,
         sample=[text_of(k, d) for k in ("refunds-1", "refunds-3", "sso-1", "webhooks-1", "api-1", "export-4")])


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, max_tokens: int = 220,
           echo: bool = True) -> dict | None:
    """One call through MemoryRouter. `body` holds the system message."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["chat", prompt, "--model", MODEL["model"], "--max-tokens", str(max_tokens), "--temperature", "0",
            "--body", str(body.relative_to(HERE))]
    if boundary:
        args += ["--boundary", boundary]
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
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    text = res["choices"][0]["message"]["content"] or ""
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct)


def write_body(name: str, system: str) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    # the same seed for every call in both wirings: the model's own sampling noise is as small as it gets
    body = {"model": MODEL["model"], "seed": 7, "messages": [{"role": "system", "content": system}]}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def pinned_system(d: dict, prompt: str, snap: list[dict]) -> str:
    return d["prompts"][prompt] + "\n\nRelevant memory:\n" + "\n".join(f"- {s['text']}" for s in snap)


def call(cli: CLI, q: dict, prompt: str, arm: str, world: dict | None = None, echo: bool = False) -> dict:
    """One graded call. arm = router (production wiring, `--boundary`) or pinned (the question's snapshot)."""
    if arm == "router":
        r = router(cli, q["say"], OUT / f"body-{prompt}.json", boundary=world["boundary"], echo=echo)  # type: ignore[index]
    else:
        r = router(cli, q["say"], OUT / f"body-pin-{prompt}-{q['id']}.json", echo=echo)
    if not r:
        return dict(q=q["id"], arm=arm, prompt=prompt, ok=None, prompt_tokens=0, reply="", missing=[], missing_facts=[], wrong=[])
    return dict(q=q["id"], arm=arm, prompt=prompt, reply=r["reply"], prompt_tokens=r["prompt_tokens"], **grade(r["reply"], q))


def run_calls(cli: CLI, q: dict, plan: list[tuple[str, str]], world: dict, echo_first: bool = False) -> list[dict]:
    """Several calls for one question in parallel (the first echoed when asked), results in plan order."""
    if echo_first:
        first = call(cli, q, plan[0][0], plan[0][1], world, echo=True)
        rest = plan[1:]
    else:
        first, rest = None, plan
    with ThreadPoolExecutor(max(1, min(6, len(rest)))) as pool:
        got = list(pool.map(lambda p: call(cli, q, p[0], p[1], world), rest))
    return ([first] if first else []) + got


def why(row: dict) -> str:
    if row["ok"] is not False:
        return ""
    parts = [f"missing: {m}" for m in row["missing"]] + [f"wrong: {w}" for w in row["wrong"]]
    return "; ".join(parts)


# --------------------------------------------------------------------------- step 4: the eval as wired in production

def eval_ab_router(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    for p in PROMPTS:
        write_body(p, d["prompts"][p])
    rows = []
    for n, q in enumerate(d["questions"]):
        got = run_calls(cli, q, [("v1", "router"), ("v2", "router")], world, echo_first=n == 0)
        if n == 0:
            note("every other call is the same command; v2 differs only in the system prompt (out/body-v2.json)")
        for r in got:
            r["phase"] = "ab"
            emit("router_row", "", **r)
        rows += got
        v1, v2 = got
        flag = "" if v1["ok"] == v2["ok"] else ("   ← v2 broke it" if v1["ok"] else "   ← v2 fixed it")
        emit("text", f"    {q['id']} {q['topic']:<17} v1 {marks([v1])} ({v1['prompt_tokens']})   v2 {marks([v2])} ({v2['prompt_tokens']}){flag}")
    n = len(d["questions"])
    if all(r["ok"] is not None for r in rows):
        v1 = sum(r["ok"] for r in rows if r["prompt"] == "v1")
        v2 = sum(r["ok"] for r in rows if r["prompt"] == "v2")
        broke, fixed = ab_verdict(rows)
        emit("text", f"\n    v1 {v1}/{n} · v2 {v2}/{n} — read naively: v2 broke {', '.join(broke) or 'nothing'}"
                     f" and fixed {', '.join(fixed) or 'nothing'}")
    return rows


def ab_verdict(ab: list[dict]) -> tuple[list[str], list[str]]:
    """Questions where v2 lost / gained a ✓ against v1, one run each (step 4)."""
    by = {(r["q"], r["prompt"]): r["ok"] for r in ab}
    qs = sorted({r["q"] for r in ab})
    broke = [q for q in qs if by[(q, "v1")] and by[(q, "v2")] is False]
    fixed = [q for q in qs if by[(q, "v1")] is False and by[(q, "v2")]]
    return broke, fixed


# --------------------------------------------------------------------------- step 5: A/A — the same eval again

def eval_aa_router(cli: CLI, world: dict, ab: list[dict]) -> dict:
    d = load_data()
    per = {}
    for n, q in enumerate(d["questions"]):
        first = next(r for r in ab if r["q"] == q["id"] and r["prompt"] == "v1")
        got = run_calls(cli, q, [("v1", "router")] * (AA_RUNS - 1), world, echo_first=n == 0)
        for r in got:
            r["phase"] = "aa"
            emit("router_row", "", **r)
        rs = [first] + got
        per[q["id"]] = rs
        tail = ("   ← memory differed, grade flipped" if flipped(rs) and moved(rs) else
                "   ← grade flipped, same memory size" if flipped(rs) else
                "   ← memory differed" if moved(rs) else "")
        emit("text", f"    {q['id']} {q['topic']:<17} v1 ×{AA_RUNS}: {marks(rs)}   prompt tokens {toks(rs)}{tail}")
    nm = sum(moved(rs) for rs in per.values())
    nf = sum(flipped(rs) for rs in per.values())
    emit("text", f"\n    nothing changed between these {AA_RUNS} runs — same prompt, same 60 facts, same boundary:\n"
                 f"    {nm}/{len(per)} questions got a different amount of memory (prompt tokens), {nf}/{len(per)} changed grade")
    note("the reply does not say which memories MemoryRouter attached; `usage.prompt_tokens` minus the same call "
         "without a boundary is the only trace")
    emit("aa", "", moved=nm, flipped=nf, runs=AA_RUNS)
    return dict(per=per, moved=nm, flipped=nf)


# --------------------------------------------------------------------------- step 6: pin the memory

def pin(cli: CLI, world: dict) -> dict:
    d = load_data()
    snaps = {}
    for n, q in enumerate(d["questions"]):
        res = cli.run("search", q["say"], "--projects", world["project"], "--types", "fact",
                      "--top-k", str(SNAPSHOT_K), scoped=True, echo=n == 0) or {}
        hits = res.get("facts") or []
        snaps[q["id"]] = [dict(id=h["id"], key=key_of(h["fact"], d), text=h["fact"]) for h in hits]
    OUT.mkdir(exist_ok=True)
    doc = dict(pinned_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), project=world["project"],
               k=SNAPSHOT_K, questions=snaps)
    (OUT / "snapshots.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    q1 = d["questions"][0]
    emit("text", f"\n  {q1['id']} snapshot ({len(snaps[q1['id']])} facts, ids and text saved to out/snapshots.json):\n"
         + "\n".join(f"    {s['id'][:13]}…  [{s['key']}] {s['text']}" for s in snaps[q1["id"]]))
    lines = []
    for q in d["questions"]:
        keys = {s["key"] for s in snaps[q["id"]]}
        need = sorted({k for m in q["must"] for k in m["facts"]})
        have = [k for k in need if k in keys]
        lines.append(f"    {q['id']} {q['topic']:<17} {len(snaps[q['id']])} facts · the grader's facts {len(have)}/{len(need)} in it"
                     + ("" if len(have) == len(need) else "   ← missing " + ", ".join(k for k in need if k not in keys)))
    emit("text", "\n" + "\n".join(lines))
    for p in PROMPTS:
        for q in d["questions"]:
            write_body(f"pin-{p}-{q['id']}", pinned_system(d, p, snaps[q["id"]]))
    note("each prompt version gets the same snapshot in its system prompt (out/body-pin-<prompt>-<question>.json); "
         "no boundary, so nothing is picked per call")
    emit("snapshot", "", **doc)
    return snaps


# --------------------------------------------------------------------------- step 7: the eval against the snapshot

def eval_pinned(cli: CLI, world: dict) -> dict:
    d = load_data()
    per = {}
    for n, q in enumerate(d["questions"]):
        plan = [("v1", "pinned")] * AA_RUNS + [("v2", "pinned")] * AA_RUNS
        got = run_calls(cli, q, plan, world, echo_first=n == 0)
        if n == 0:
            note(f"{AA_RUNS} × v1 and {AA_RUNS} × v2 per question, all against that question's snapshot")
        for r in got:
            emit("pinned_row", "", **r)
        v1 = [r for r in got if r["prompt"] == "v1"]
        v2 = [r for r in got if r["prompt"] == "v2"]
        per[q["id"]] = dict(v1=v1, v2=v2)
        tail = "   ← v1 flipped" if flipped(v1) else ""
        tail += "   ← v2 flipped" if flipped(v2) else ""
        emit("text", f"    {q['id']} {q['topic']:<17} v1 {marks(v1)} ({toks(v1)})   v2 {marks(v2)} ({toks(v2)}){tail}")
    nm = sum(moved(x["v1"]) or moved(x["v2"]) for x in per.values())
    nf = sum(flipped(x["v1"]) for x in per.values())
    ok1 = sum(r["ok"] for x in per.values() for r in x["v1"] if r["ok"])
    ok2 = sum(r["ok"] for x in per.values() for r in x["v2"] if r["ok"])
    major = lambda rs: sum(bool(r["ok"]) for r in rs) * 2 > len(rs)  # noqa: E731
    diff = [qid for qid, x in per.items() if x["v1"][0]["ok"] is not None and major(x["v1"]) != major(x["v2"])]
    emit("text", f"\n    memory moved on {nm}/{len(per)} questions (prompt tokens) · v1 changed grade on {nf}/{len(per)} across its {AA_RUNS} runs\n"
                 f"    v1 {ok1}/{len(per) * AA_RUNS} · v2 {ok2}/{len(per) * AA_RUNS}; questions where v1 and v2 disagree "
                 f"(majority of {AA_RUNS}): {', '.join(diff) or 'none'} — with the memory fixed, that is the prompt")
    if nf:
        note("those flips came with identical prompt tokens — the same memory, word for word: that is the model's own "
             "sampling noise, now visible apart from the memory")
    emit("pinned", "", moved=nm, flipped=nf, v1=ok1, v2=ok2, differ=diff, runs=AA_RUNS)
    return dict(per=per, moved=nm, flipped=nf, v1=ok1, v2=ok2, differ=diff)


# --------------------------------------------------------------------------- step 8: attribution + scoreboard

def attribute(snaps: dict, pinned: dict, aa: dict, ab: list[dict]) -> dict:
    d = load_data()
    out = []
    counts = dict(retrieval=0, generation=0, unknown=0)
    for q in d["questions"]:
        keys = {s["key"] for s in snaps[q["id"]]}
        fails = [r for r in pinned["per"][q["id"]]["v1"] + pinned["per"][q["id"]]["v2"] if r["ok"] is False]
        for r in fails:
            gone = [k for k in r["missing_facts"] if k not in keys]
            cause = "retrieval" if gone else "generation"
            counts[cause] += 1
            out.append(dict(q=q["id"], arm="pinned", prompt=r["prompt"], cause=cause, why=why(r),
                            facts=gone or r["missing_facts"]))
        rfails = [r for r in aa["per"][q["id"]] + [x for x in ab if x["q"] == q["id"] and x["prompt"] == "v2"] if r["ok"] is False]
        counts["unknown"] += len(rfails)
        for r in rfails:
            out.append(dict(q=q["id"], arm="router", prompt=r["prompt"], cause="unknown", why=why(r), facts=[],
                            prompt_tokens=r["prompt_tokens"]))
    lines = []
    for q in d["questions"]:
        mine = [a for a in out if a["q"] == q["id"] and a["arm"] == "pinned"]
        if not mine:
            continue
        a = mine[0]
        lines.append(f"    {q['id']} {q['topic']:<17} snapshot: {len(mine)} wrong answer(s) → {a['cause']} failure")
        for k in a["facts"]:
            where = "in the snapshot, the answer left it out" if a["cause"] == "generation" else "not in the snapshot"
            lines.append(f"         [{k}] {text_of(k, d)}  ({where})")
    ru = [a for a in out if a["arm"] == "router"]
    if ru:
        lines.append(f"    MemoryRouter wiring: {len(ru)} wrong answer(s) across steps 4–5 → cause unknown: the call kept no record "
                     "of what it was given, only a prompt-token count (" + ", ".join(sorted({a['q'] for a in ru})) + ")")
    emit("text", "\n".join(lines) if lines else "    no wrong answers this run")
    emit("attribution", "", rows=out, counts=counts)
    return dict(rows=out, counts=counts)


def scoreboard(ab: list[dict], aa: dict, pinned: dict, attr: dict) -> dict:
    d = load_data()
    n = len(d["questions"])
    head = f"    {'':<22}{'Router v1 ×3':>14}{'Router v2':>11}{'snapshot v1 ×3':>16}{'snapshot v2 ×3':>16}   cause"
    lines = [head]
    for q in d["questions"]:
        r1 = aa["per"][q["id"]]
        r2 = [x for x in ab if x["q"] == q["id"] and x["prompt"] == "v2"]
        p = pinned["per"][q["id"]]
        causes = sorted({a["cause"] for a in attr["rows"] if a["q"] == q["id"] and a["arm"] == "pinned"})
        lines.append(f"    {q['id'] + ' ' + q['topic']:<22}{marks(r1):>14}{marks(r2):>11}{marks(p['v1']):>16}{marks(p['v2']):>16}   "
                     + (", ".join(causes) or "–"))
    lines.append(f"\n    {'same memory every call':<40}MemoryRouter {n - aa['moved']}/{n} questions · snapshot {n - pinned['moved']}/{n}")
    lines.append(f"    {'A/A: grade changed, nothing else did':<40}MemoryRouter {aa['flipped']}/{n} · snapshot {pinned['flipped']}/{n}")
    nr = attr["counts"]["unknown"]
    np_ = attr["counts"]["retrieval"] + attr["counts"]["generation"]
    lines.append(f"    {'wrong answers with a known cause':<40}MemoryRouter 0/{nr} · snapshot {np_}/{np_}"
                 f" ({attr['counts']['retrieval']} retrieval, {attr['counts']['generation']} generation)")
    emit("text", "\n".join(lines))
    sc = dict(router_moved=aa["moved"], router_flipped=aa["flipped"], pinned_moved=pinned["moved"],
              pinned_flipped=pinned["flipped"], counts=attr["counts"], differ=pinned["differ"],
              pinned_v1=pinned["v1"], pinned_v2=pinned["v2"])
    emit("scoreboard", "", **sc)
    return sc


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    banner(2, TOTAL, "Set up — the support assistant's memory: one project, one boundary")
    world = setup(cli)

    banner(3, TOTAL, "What the assistant knows — 60 support facts")
    store(cli, world)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`

    banner(4, TOTAL, "The eval as wired in production — 8 questions, prompt v1 vs v2, through MemoryRouter")
    note("`router chat --boundary` (gpt-4o-mini, temperature 0); each answer is graded against the facts it must state")
    ab = eval_ab_router(cli, world)

    banner(5, TOTAL, f"A/A — prompt v1 {AA_RUNS} times, nothing changed")
    aa = eval_aa_router(cli, world, ab)

    banner(6, TOTAL, "Pin the memory — retrieve once per question, save the snapshot")
    snaps = pin(cli, world)

    banner(7, TOTAL, "The eval against the snapshot — v1 and v2, the memory held still")
    pinned = eval_pinned(cli, world)
    if cli.router_ok:  # type: ignore[attr-defined]
        broke, fixed = ab_verdict(ab)
        emit("text", f"    step 4 (through MemoryRouter) read: v2 broke {', '.join(broke) or 'nothing'}, fixed {', '.join(fixed) or 'nothing'}"
                     f"  ·  against the snapshot: v1 and v2 differ on {', '.join(pinned['differ']) or 'nothing'}")

    banner(8, TOTAL, "Why each answer failed — and the scoreboard")
    attr = attribute(snaps, pinned, aa, ab)
    sc = scoreboard(ab, aa, pinned, attr)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(ab=ab, aa=aa, pinned=pinned, attribution=attr, scoreboard=sc, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every call, answer and grade: {(OUT / 'report.json').relative_to(HERE)}; snapshots: out/snapshots.json")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def read_snapshots() -> dict:
    p = OUT / "snapshots.json"
    if not p.exists():
        die("no snapshot yet; run `python3 demo.py` first")
    return json.loads(p.read_text(encoding="utf-8"))


def show_snapshot(cli: CLI, qid: str) -> dict:
    """One question's pinned memory, its grader, and whether every pinned fact is still in memory as pinned."""
    d = load_data()
    q = pick_question(d, qid)
    doc = read_snapshots()
    snap = doc["questions"][q["id"]]
    world = load_world(cli)
    live = {f["id"]: f["fact"] for f in list_facts(cli, world["project"])}
    same = sum(1 for s in snap if live.get(s["id"]) == s["text"])
    emit("text", f"\n  {q['id']}: {q['say']}\n  pinned {doc['pinned_at']}, {len(snap)} facts:\n"
         + "\n".join(f"    {s['id'][:13]}…  [{s['key']}] {s['text']}" for s in snap))
    emit("text", "  a right answer must state: " + "; ".join(m["label"] for m in q["must"])
         + ("" if not q["forbid"] else "  ·  must not: " + "; ".join(f["label"] for f in q["forbid"])))
    emit("text", f"  still in memory word for word: {same}/{len(snap)}"
         + ("" if same == len(snap) else "  ← memory changed since the snapshot; the replay still uses the pinned text"))
    return dict(q=q["id"], snapshot=snap, unchanged=same)


def replay(cli: CLI, qid: str, prompt: str, arm: str) -> dict:
    """One question, once, against its snapshot (or through MemoryRouter), graded."""
    d = load_data()
    q = pick_question(d, qid)
    world = load_world(cli)
    if arm == "pinned":
        snap = read_snapshots()["questions"][q["id"]]
        write_body(f"pin-{prompt}-{q['id']}", pinned_system(d, prompt, snap))
    else:
        write_body(prompt, d["prompts"][prompt])
    r = call(cli, q, prompt, arm, world, echo=True)
    if r["ok"] is None:
        die("MemoryRouter is not available on this account (no balance)")
    label = "against the snapshot" if arm == "pinned" else "through MemoryRouter (--boundary)"
    emit("text", f"\n  {q['id']} {prompt} {label}: {'✓' if r['ok'] else '✗'}   prompt tokens {r['prompt_tokens']}"
         + (f"   {why(r)}" if r["ok"] is False else "") + "\n    "
         + "\n    ".join(textwrap.wrap(r["reply"], 96)))
    emit("replay", "", **r)
    return r


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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "snapshot", "replay", "cleanup"],
                    help="run = full demo (default); snapshot <Qn> = one question's pinned memory; "
                         "replay <Qn> = one call against the snapshot (or --router); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for snapshot / replay: the question id (Q1 … Q8)")
    ap.add_argument("--prompt", choices=PROMPTS, default="v1", help="for replay: the prompt version")
    ap.add_argument("--router", action="store_true", help="for replay: through MemoryRouter instead of the snapshot")
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
    if args.command in ("snapshot", "replay"):
        if len(args.args) != 1:
            die(f"usage: python3 demo.py {args.command} <Q1…Q8>")
        if args.command == "snapshot":
            banner(2, 2, f"The pinned memory for {args.args[0]}")
            show_snapshot(cli, args.args[0])
        else:
            banner(2, 2, f"Replay {args.args[0]} with prompt {args.prompt}")
            replay(cli, args.args[0], args.prompt, "router" if args.router else "pinned")
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py snapshot Q3` or `python3 demo.py replay Q3 --prompt v2`; "
          "`python3 demo.py cleanup` removes the demo data.")


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

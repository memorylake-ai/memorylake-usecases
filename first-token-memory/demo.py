#!/usr/bin/env python3
"""first-token-memory — stream an agent's answer fast without dropping its memory.

Tallow Labs (fictional) puts a coding assistant, Quill, in Priya's editor. Answers stream, and the team's UX rule is
that the first token shows within 2.5 s. Priya's memory is 8 facts (pnpm, Vitest, Fly.io, Drizzle, Hono, Neon, …).
Six everyday questions go through three ways of getting that memory into a streamed answer:

  A  retrieve, then stream     — `router chat --stream --boundary`: MemoryRouter looks the memory up, then the
                                 model starts. Right answers; the first token waits for the lookup.
  B  1 s deadline              — `search` with a 1.0 s deadline; when it misses, the answer streams without memory
                                 and the search is left to finish. Fast first token — and the search, a couple of
                                 seconds later, comes back holding the very fact the answer just got wrong.
  C  prefetch at session open  — `fact list --actors` once when the chat opens, while Priya is still typing; every
                                 answer streams with that profile in the prompt.

Nothing in B fails: no error, no exception, a fast first token every time. The demo prints the signal B never
logs — the deadline missed, how late the search came back, and whether it held the answer's fact.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.
Model calls go through MemoryRouter (gpt-4o-mini, 18 short answers a run) and are billed to your MemoryLake balance.
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ftk"  # memorylake-usecases / first-token-memory


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

    # `fact update` is retried too: setting the same text twice is the same as once.
    READ_ONLY = {"get", "list", "trace", "status", "current", "me", "search", "update"}
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
        # A multi-line argument is shown as its first line; it is printed in full
        # once, above the call.
        shown = [a.split("\n")[0] + f" […{a.count(chr(10))} more lines]" if "\n" in a else a for a in shown]
        # `fact add` with many texts: the first two, then a count (display only; every text is sent).
        if shown[:2] == ["fact", "add"] and len(shown) > 12:
            # keep the flags after the texts (--project, --workspace): they say where the facts go
            cut = next((i for i in range(2, len(shown)) if shown[i].startswith("--")), len(shown))
            tail = shown[cut:]
            body = shown[:cut]
            shown = body[:6] + [f"… {len(body) - 6} more texts"] + tail
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
                parsed = self._parse(out)
                # A reply that should be JSON and isn't (seen once on `lib upload`, exit 0): retry what is safe to retry.
                if parsed is not None or not out.strip() or not retryable or attempt >= 4 or args[0] == "router":
                    break
                attempt += 1
                note(f"unexpected reply ({' '.join(out.split())[:80]}); retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        parsed = self._parse(out)
        return parsed if parsed is not None else (out if out.strip() else None)

    @staticmethod
    def _parse(out: str):
        """The JSON reply, also when something was printed before it; None when there is no JSON."""
        for start in (0, out.find("{"), out.find("[")):
            if start < 0:
                continue
            try:
                return json.loads(out[start:])
            except json.JSONDecodeError:
                continue
        return None

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
    rc, out, _ = cli.raw("router", "chat", "--help", echo=False)
    if rc != 0 or "--stream" not in out or "--body" not in out:
        die("this memorylake CLI has no `router chat --stream --body`; it needs v20261015 or later.\n"
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
    return cli




# --------------------------------------------------------------------------- settings

TOTAL = 6             # steps in a full run
ARMS = ("A", "B", "C")


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=echo)


def find_boundary(cli: CLI, echo: bool = False):
    items = paged(cli, ["boundary", "list", "--page-size", "100"], echo)
    return next((b for b in items if b.get("custom_id") == cid("boundary")), None)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def actor_facts(cli: CLI, actor: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--actors", actor, "--page-size", "200"], echo)


def delete_fact(cli: CLI, fact_id: str, scope: list[str], echo: bool = False) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and `fact delete` then
    fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes first, the list must come back empty,
    then the actor. A sealed (unbound) actor answers 404 to `fact list`, so it is bound back first."""
    try:
        found = actor_facts(cli, actor_id)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):  # the binding takes a moment to show
            try:
                found = actor_facts(cli, actor_id)
                break
            except CLIError as e2:
                if not e2.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    left = found
    for attempt in range(4):
        pmap(lambda f: delete_fact(cli, f["id"], ["--actor", actor_id]), left)
        left = actor_facts(cli, actor_id)
        if not left:
            break
        time.sleep(2 * (attempt + 1))
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)


def pmap(fn, items, workers: int = 4) -> list:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def save(name: str, obj) -> Path:
    p = OUT / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load(name: str):
    p = OUT / f"{name}.json"
    if not p.exists():
        die(f"no out/{name}.json yet; run `python3 demo.py` first")
    return json.loads(p.read_text(encoding="utf-8"))


def verdict(q: dict, answer: str) -> str:
    """used = the answer names what Priya's memory says; contradicts = it names something else instead; generic = neither."""
    a = answer.lower()
    if re.search(q["expect"], a):
        return "used"
    if re.search(q["contra"], a):
        return "contradicts"
    return "generic"


MARK = {"used": "✓ used memory", "contradicts": "✗ contradicts memory", "generic": "– generic (no memory)"}


def check_story() -> None:
    """The rules on the story itself, before any call is made: each question's expected word is in its fact, and no
    fact names a word that would count as contradicting it."""
    d = load_data()
    facts = {f["key"]: f["text"] for f in d["facts"]}
    for q in d["questions"]:
        if q["fact"] not in facts or verdict(q, facts[q["fact"]]) != "used":
            die(f"story self-check failed: {q['id']}'s fact does not name {q['expect_label']}")
    for q in d["questions"]:  # offline cases for the grader
        assert verdict(q, f"use {q['expect_label']} for this") == "used", q["id"]
        assert verdict(q, "here is a plain answer") == "generic", q["id"]


# --------------------------------------------------------------------------- streaming a model call

def stream(cli: CLI, arm: str, q: dict, args: list[str], t0: float, echo: bool) -> dict:
    """Run a `router … --stream` call, emit its text as it arrives, and time the first token from t0."""
    if echo:
        cli._echo([a.replace(str(HERE) + os.sep, "") for a in args])   # the body file, relative to the demo
    proc = subprocess.Popen([cli.binary, *args], env=cli.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, line, first = [], "", None
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if first is None and ch.strip():
            first = time.time() - t0
            emit("first", "", arm=arm, qid=q["id"], secs=round(first, 2))
        out.append(ch)
        if ch == "\n":
            if line.strip():
                emit("text", "    │ " + line)
            emit("stream", "", arm=arm, qid=q["id"], chunk=line + "\n")
            line = ""
        else:
            line += ch
            if len(line) >= 92 and ch == " ":
                emit("text", "    │ " + line)
                emit("stream", "", arm=arm, qid=q["id"], chunk=line)
                line = ""
    if line:
        emit("text", "    │ " + line)
        emit("stream", "", arm=arm, qid=q["id"], chunk=line)
    err = proc.stderr.read()
    if proc.wait() != 0:
        e = CLIError([cli.binary, *args], proc.returncode, "".join(out), err)
        if e.has("402", "insufficient"):
            die("MemoryRouter answered 402 (insufficient quota): this demo needs model calls on your MemoryLake balance")
        raise e
    return dict(text="".join(out).strip(), first=round(first if first is not None else time.time() - t0, 2),
                done=round(time.time() - t0, 2))


def router_args(d: dict, q: dict) -> list[str]:
    return ["router", "chat", q["ask"], "--model", d["model"], "--max-tokens", str(d["max_tokens"]), "--temperature", "0", "--stream"]


def body_file(d: dict, profile: list[str] | None, name: str) -> Path:
    """A whole request body (`--body`): the system prompt, plus what Quill knows about Priya when there is any."""
    system = d["system"]
    if profile:
        system += "\n\nWhat you know about Priya (use it; it overrides defaults):\n" + "\n".join(f"- {t}" for t in profile)
    p = OUT / f"body-{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": d["model"], "messages": [{"role": "system", "content": system}]}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def search_hits(cli: CLI, q: dict, actor: str, echo: bool) -> list[str]:
    r = cli.run("search", q["ask"], "--actors", actor, "--types", "fact", "--top-k", "5", scoped=True, echo=echo) or {}
    return [(f.get("fact") or f.get("text") or "") for f in (r.get("facts") or [])]


# --------------------------------------------------------------------------- the three ways

def ask(cli: CLI, d: dict, world: dict, arm: str, q: dict, profile: list[str] | None, echo: bool) -> dict:
    head = f"\n  {q['id']}  “{q['ask']}”"
    emit("text", head)
    emit("asked", "", arm=arm, qid=q["id"], ask=q["ask"])
    budget = d["budget_secs"]
    late = None
    t0 = time.time()
    if arm == "A":
        r = stream(cli, arm, q, router_args(d, q) + ["--system", d["system"], "--boundary", world["boundary"]], t0, echo)
        how = "MemoryRouter looked up the memory first"
    elif arm == "C":
        r = stream(cli, arm, q, router_args(d, q) + ["--body", str(body_file(d, profile, "C"))], t0, echo)
        how = "prefetched profile in the prompt"
    else:
        box: dict = {}

        def lookup():
            t = time.time()
            try:
                box["hits"] = search_hits(cli, q, world["actor"], echo)
            except CLIError as e:
                box["error"] = str(e)
            box["secs"] = time.time() - t

        th = threading.Thread(target=lookup, daemon=True)
        th.start()
        th.join(d["deadline_secs"])
        if th.is_alive():
            r = stream(cli, arm, q, router_args(d, q) + ["--body", str(body_file(d, None, "B"))], t0, echo)
            how = f"deadline {d['deadline_secs']:.1f} s missed → answered without memory"
            th.join()
            fact = next(f["text"] for f in d["facts"] if f["key"] == q["fact"])
            held = fact in box.get("hits", [])
            late = dict(secs=round(box.get("secs", 0), 2), held=held, fact=fact, error=box.get("error"))
        else:
            r = stream(cli, arm, q, router_args(d, q) + ["--body", str(body_file(d, box.get("hits"), "B"))], t0, echo)
            how = f"search back in {box.get('secs', 0):.2f} s, inside the deadline"
    v = verdict(q, r["text"])
    ok = r["first"] <= budget
    emit("text", f"    first token {r['first']:.2f} s {'✓' if ok else '✗'} (budget {budget:.1f} s) · done {r['done']:.2f} s · "
                 f"{MARK[v]}{'' if v == 'used' else ' — memory says ' + q['expect_label']}")
    if late:
        if late.get("error"):
            emit("text", f"    the search failed after {late['secs']:.2f} s: {late['error'][:80]}")
        else:
            when = ("before the answer's first token" if late["secs"] < r["first"] else
                    "while the answer was streaming" if late["secs"] < r["done"] else "after the answer had streamed")
            late["when"] = when
            emit("text", f"    the search came back at {late['secs']:.2f} s, {when}, "
                         + (f"holding “{late['fact']}” — the deadline had already decided: discarded" if late["held"]
                            else "without the fact the answer needed"))
    res = dict(arm=arm, qid=q["id"], answer=r["text"], first=r["first"], done=r["done"], within=ok, verdict=v, how=how, late=late)
    emit("answered", "", **res)
    return res


def run_arm(cli: CLI, d: dict, world: dict, arm: str) -> dict:
    profile = None
    prefetch = None
    if arm == "C":
        note("the chat opens: Quill loads Priya's profile while she is still typing")
        t = time.time()
        facts = actor_facts(cli, world["actor"], echo=True)
        prefetch = round(time.time() - t, 2)
        profile = [f.get("fact") or f.get("text") for f in facts]
        emit("text", f"    prefetched {len(profile)} facts in {prefetch:.2f} s — before the first question")
        emit("prefetch", "", secs=prefetch, facts=profile)
    rows = [ask(cli, d, world, arm, q, profile, echo=i == 0) for i, q in enumerate(d["questions"])]
    return dict(arm=arm, rows=rows, prefetch=prefetch)


# --------------------------------------------------------------------------- step 2: memory

def leftovers(cli: CLI) -> bool:
    return bool(find_actor(cli, load_data()["user"]["key"]) or find_boundary(cli))


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    u = d["user"]
    actor = cli.run("actor", "create", "--custom-id", cid(u["key"]), "--display-name", u["display"],
                    "--description", f"{u['role']}. Demo data.", "--tags", "demo", echo=True)["id"]
    cli.run("actor", "bind", "--actor", actor, scoped=True, echo=True)
    r = cli.run("fact", "add", *[f["text"] for f in d["facts"]], "--actor", actor, scoped=True, echo=True) or {}
    got = r.get("facts") or []
    if len(got) != len(d["facts"]):
        die(f"`fact add` returned {len(got)} ids for {len(d['facts'])} texts")
    boundary = cli.run("boundary", "create", "--name", f"{d['assistant']} · {u['display']}", "--custom-id", cid("boundary"),
                       "--human-actor", actor, scoped=True, echo=True)["id"]
    emit("text", "\n    key      fact")
    for f in d["facts"]:
        emit("text", f"    {f['key']:<8} {f['text']}")
    note(f"{len(got)} facts on {u['display']}'s actor; boundary {boundary} scopes MemoryRouter to her")
    world = dict(actor=actor, boundary=boundary, ids={f["key"]: g["id"] for f, g in zip(d["facts"], got)})
    save("world", world)
    emit("setup", "", facts=d["facts"], **world)
    return world


# --------------------------------------------------------------------------- step 6: score

def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 2)


def score(d: dict, arms: dict) -> dict:
    st = d["arms"]
    n = len(d["questions"])
    rows = {}
    emit("text", f"\n    {'':<30} {'first token: median · max':<27} {'within ' + format(d['budget_secs'], '.1f') + ' s':<11} "
                 f"{'used memory':<12} {'contradicted it':<16}")
    for a in ARMS:
        rs = arms[a]["rows"]
        firsts = [r["first"] for r in rs]
        row = dict(median=median(firsts), max=max(firsts), within=sum(r["within"] for r in rs),
                   used=sum(r["verdict"] == "used" for r in rs), contradicts=sum(r["verdict"] == "contradicts" for r in rs),
                   late_held=sum(1 for r in rs if r["late"] and r["late"]["held"]), missed=sum(1 for r in rs if r["late"]),
                   prefetch=arms[a]["prefetch"])
        rows[a] = row
        emit("text", f"    {st[a]['short']:<30} {format(row['median'], '.2f') + ' s · ' + format(row['max'], '.2f') + ' s':<27} "
                     f"{str(row['within']) + '/' + str(n):<11} {str(row['used']) + '/' + str(n):<12} {str(row['contradicts']) + '/' + str(n):<16}")
    b = rows["B"]
    if b["missed"]:
        note(f"B missed its {d['deadline_secs']:.1f} s deadline on {b['missed']}/{n} questions; on {b['late_held']} of them the search "
             "came back holding the fact the answer needed — and nothing raised an error")
    if rows["C"]["prefetch"] is not None:
        note(f"C paid {rows['C']['prefetch']:.2f} s once, when the chat opened — not on any answer")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, budget=d["budget_secs"], deadline=d["deadline_secs"],
                  model=d["model"], rows=rows, arms=arms)
    save("report", report)
    emit("score", "", rows=rows, budget=d["budget_secs"], deadline=d["deadline_secs"], n=n)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, f"Memory — {len(d['facts'])} facts about {d['user']['display']}, and a boundary for MemoryRouter")
    world = setup(cli, d)
    arms = {}
    titles = {"A": "A — retrieve, then stream (MemoryRouter looks the memory up first)",
              "B": f"B — a {d['deadline_secs']:.1f} s deadline on retrieval, then stream without it",
              "C": "C — prefetch the profile when the chat opens, stream with it"}
    for i, a in enumerate(ARMS):
        banner(3 + i, TOTAL, titles[a])
        note(f"{d['arms'][a]['how'].replace('`', '')}")
        arms[a] = run_arm(cli, d, world, a)
    banner(6, TOTAL, f"Score — first token within {d['budget_secs']:.1f} s, and did the answer use the memory?")
    return score(d, arms)


# --------------------------------------------------------------------------- read-only subcommand

def timed(fn) -> float:
    t = time.time()
    fn()
    return round(time.time() - t, 2)


def show_latency(cli: CLI) -> None:
    """Five timings of each read the three ways make: search (B), the whole profile (C), and one routed answer
    with and without memory (A against the no-memory floor)."""
    d = load_data()
    a = find_actor(cli, d["user"]["key"], echo=True)
    b = find_boundary(cli)
    if not a or not b:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    q = d["questions"][1]
    s = [timed(lambda i=i: search_hits(cli, q, a["id"], echo=i == 0)) for i in range(5)]
    f = [timed(lambda i=i: actor_facts(cli, a["id"], echo=i == 0)) for i in range(5)]
    emit("text", "")
    firsts = {}
    for name, extra in (("no memory", ["--body", str(body_file(d, None, "latency"))]),
                        ("--boundary", ["--system", d["system"], "--boundary", b["id"]])):
        firsts[name] = []
        for i in range(2):
            emit("text", f"\n  {name}, try {i + 1}")
            firsts[name].append(stream(cli, "L", q, router_args(d, q) + extra, time.time(), echo=i == 0)["first"])
    emit("text", f"\n  search --actors (top 5 facts)      {'  '.join(f'{x:.2f}' for x in s)}   s")
    emit("text", f"  fact list --actors (whole profile)  {'  '.join(f'{x:.2f}' for x in f)}   s")
    for name, xs in firsts.items():
        emit("text", f"  router chat --stream {name:<12}  first token {'  '.join(f'{x:.2f}' for x in xs)}   s")
    note("times are measured around each CLI call, so they include starting the CLI process")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    b = find_boundary(cli, echo=not quiet)
    if b:
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    nfacts = 0
    a = find_actor(cli, d["user"]["key"], echo=not quiet)
    if a:
        nfacts = delete_actor(cli, a["id"])
    if not quiet:
        note(f"deleted {1 if b else 0} boundary, {nfacts} facts and {1 if a else 0} actor (facts first: a deleted actor's "
             "facts could never be removed)" if (a or b) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "world"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(parents=True, exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "latency", "cleanup"],
                    help="run = full demo (default); latency = time each read five times; cleanup = delete everything")
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
    if args.command == "latency":
        banner(2, 2, "Latency — each way's read, timed five times")
        show_latency(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py latency`; `python3 demo.py cleanup` removes the demo data.")


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

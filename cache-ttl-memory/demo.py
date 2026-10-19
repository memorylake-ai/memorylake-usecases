#!/usr/bin/env python3
"""cache-ttl-memory — a coding agent's memory survives the developer's coffee break; its prompt cache, by default,
does not.

Saltmarrow Pay (a fictional payments company) runs a coding agent, Kit, on claude-haiku-4-5 over MemoryRouter. Every
request carries the same long repository guide (repo map, conventions, owners) with an Anthropic `cache_control`
breakpoint, and MemoryRouter attaches what the team has decided in past sessions (`router messages --boundary`).
The developer works a short session, steps away for 5½ minutes, and comes back. Every request goes through four
cache settings at once:

  A  no cache_control
  B  cache_control {"type": "ephemeral"} — the default 5-minute TTL
  C  cache_control {"type": "ephemeral", "ttl": "1h"}
  D  the default 5-minute TTL, plus one 1-token keep-alive call 4 minutes into the break

The memory is there after the break in every arm: session 2's answers use the decisions written at the end of
session 1. The cache is not: B's first request after the break writes the whole guide again at the cache-write price,
while C (written once at the 1-hour price) and D (one ping that restarted the 5-minute clock) read it. Every reply
reports cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens and cache_read_input_tokens; the
account's own `router models` list prices each part.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router calls
are billed to your MemoryLake balance (33 calls on claude-haiku-4-5 per run, about 0.21 USD). A run takes about
7 minutes, 5½ of them the break.
"""
from __future__ import annotations

import argparse
import hashlib
import random
import json
import math
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ctm"  # memorylake-usecases / cache-ttl-memory


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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat. So is `fact update`:
    # setting the same metadata twice is the same as once.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance",
                 "update"}
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
        # Everything from the first option on is kept, so the scope (`--project P`) stays visible.
        if shown[:2] == ["fact", "add"]:
            i = next((k for k in range(2, len(shown)) if shown[k].startswith("--")), len(shown))
            if i - 2 > 2:
                shown = shown[:4] + [f"… {i - 4} more texts"] + shown[i:]
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



# --------------------------------------------------------------------------- the story

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 120
BREAK = 330      # seconds between the last call of session 1 and the first of session 2: longer than 5 minutes
PING_AT = 240    # D's keep-alive ping, this many seconds after its last session-1 call
ARMS = ("plain", "m5", "h1", "ping")
SHORT = {"plain": "A", "m5": "B", "h1": "C", "ping": "D"}
ARM_LABEL = {"plain": "A  no cache_control",
             "m5": "B  cache_control, 5-minute TTL",
             "h1": "C  cache_control, ttl: 1h",
             "ping": "D  5-minute TTL + keep-alive ping"}
CACHE = {"plain": None,
         "m5": {"type": "ephemeral"},
         "h1": {"type": "ephemeral", "ttl": "1h"},
         "ping": {"type": "ephemeral"}}


def load_data() -> dict:
    return json.loads((DATA / "saltmarrow.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return "\n".join(textwrap.wrap(" ".join(text.split()), width, initial_indent=indent, subsequent_indent=indent)) or indent


def guide(d: dict, run: str, arm: str) -> str:
    """The repository guide with a first line naming this run and arm. Every arm of every run starts from its own
    bytes, so no arm reads a cache entry another arm (or an earlier run of this demo) wrote."""
    return f"Reference copy {run}-{SHORT[arm]}.\n" + d["guide"]


def body_for(d: dict, run: str, arm: str, question: str) -> dict:
    """The Anthropic Messages request each arm sends. Only the cache_control on the guide differs; MemoryRouter
    attaches the team's memory (`--boundary`) after it for every arm."""
    block = {"type": "text", "text": guide(d, run, arm)}
    if CACHE[arm]:
        block["cache_control"] = dict(CACHE[arm])
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "temperature": 0, "system": [block],
            "messages": [{"role": "user", "content": question}]}


# --------------------------------------------------------------------------- objects

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("memory"), "--by-custom-id", scoped=True, echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def demo_boundaries(cli: CLI, echo: bool = False) -> list[dict]:
    # `boundary get --by-custom-id` is not available: list and match the custom_id prefix.
    return [b for b in paged(cli, ["boundary", "list", "--page-size", "100"], echo)
            if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def project_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--projects", project, "--page-size", "200"], echo)


# --------------------------------------------------------------------------- prices

LOCK = threading.Lock()


def prices(cli: CLI, echo: bool = True) -> dict:
    """USD per million tokens for MODEL, from the account's own price list: input, cache write (5 min and 1 h),
    cache read, output."""
    res = cli.run("router", "models", echo=echo) or {}
    items = (res.get("data") or {}).get("items") or res.get("items") or []
    m = next((i for i in items if i.get("name") == MODEL), None)
    if not m:
        die(f"{MODEL} is not in `memorylake router models` on this account")
    p = m.get("price") or {}
    det = p.get("details") or {}

    def usd(node) -> float | None:
        return node.get("usd_per_million") if isinstance(node, dict) else None

    out = dict(input=usd(p.get("input")), output=usd(p.get("output")), write5=usd(det.get("cache_write_5m")),
               write1h=usd(det.get("cache_write_1h")), read=usd(det.get("cache_read")))
    if out["input"] is None or out["output"] is None:
        die(f"no input/output price for {MODEL} in `router models`")
    for k in ("write5", "write1h", "read"):
        if out[k] is None:
            out[k] = out["input"]
            note(f"{MODEL} lists no {k} price; those tokens are priced as input")
    return out


def cost(p: dict, r: dict) -> float:
    return (r["fresh"] * p["input"] + r["w5"] * p["write5"] + r["w1h"] * p["write1h"] + r["read"] * p["read"]
            + r["completion_tokens"] * p["output"]) / 1e6


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, body: dict, boundary_id: str | None = None, max_tokens: int | None = None,
           echo: bool = False) -> dict | None:
    """One `router messages --body` call. The body is a whole Anthropic Messages request (the guide with its
    cache_control, the user turn); MemoryRouter forwards it and, with --boundary, attaches memory."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    OUT.mkdir(exist_ok=True)
    with LOCK:
        cli.nbody = getattr(cli, "nbody", 0) + 1  # type: ignore[attr-defined]
        path = OUT / f"body-{cli.nbody % 12}.json"  # type: ignore[attr-defined]
    if max_tokens:
        body = dict(body, max_tokens=max_tokens)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)
    args = ["messages", "--body", str(path.relative_to(HERE)) if echo else str(path), "--model", MODEL]
    if boundary_id:
        args += ["--boundary", boundary_id]
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model calls below are skipped; the memory steps still ran. Top up in the console to see the bill.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict) or "usage" not in res:
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    fresh, out_t = u.get("input_tokens", 0), u.get("output_tokens", 0)
    written, read = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
    split = u.get("cache_creation") or {}
    w1h = split.get("ephemeral_1h_input_tokens") or 0
    w5 = split.get("ephemeral_5m_input_tokens") if "ephemeral_5m_input_tokens" in split else written - w1h
    with LOCK:
        cli.tokens["prompt"] += fresh + written + read  # type: ignore[attr-defined]
        cli.tokens["completion"] += out_t  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    reply = " ".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text").strip()
    return dict(reply=reply, fresh=fresh, written=written, w5=w5 or 0, w1h=w1h, read=read, completion_tokens=out_t,
                at=time.time(), reported="cache_creation_input_tokens" in u)


# --------------------------------------------------------------------------- step 2: set up

def add_notes(cli: CLI, project: str, notes: list[str]) -> None:
    cli.run("fact", "add", *notes, "--project", project, scoped=True)


def setup(cli: CLI) -> dict:
    d = load_data()
    if find_project(cli) or demo_boundaries(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    p = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("memory"),
                "--description", f"{d['assistant']}'s memory of {d['repo']}: decisions from past sessions. Demo data.",
                scoped=True)
    world = {"project": p["id"]}
    add_notes(cli, world["project"], d["facts_yesterday"])
    note(f"{len(d['facts_yesterday'])} session notes from yesterday pinned verbatim — what {d['assistant']} decided "
         "and why")
    b = cli.run("boundary", "create", "--name", f"{d['assistant']} repo memory", "--custom-id", cid("repo"),
                "--projects", world["project"], scoped=True)
    world["boundary"] = b["id"]
    note("one boundary over the project: every request below, in every arm, gets the team's memory from MemoryRouter")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    p = find_project(cli)
    bs = demo_boundaries(cli)
    if not p or not bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return {"project": p["id"], "boundary": bs[0]["id"]}


def show_prices(p: dict) -> None:
    emit("text", f"    {MODEL} (USD per million tokens): input {p['input']} · cache write, 5 min {p['write5']} · "
                 f"cache write, 1 h {p['write1h']} · cache read {p['read']} · output {p['output']}")
    if p["input"]:
        note(f"a guide token written to the cache costs {p['write5'] / p['input']:.2f}× a plain one with the default "
             f"5-minute TTL, {p['write1h'] / p['input']:.2f}× with ttl 1h; every read after that, "
             f"{p['read'] / p['input']:.2f}×")
    emit("prices", "", model=MODEL, **p)


# --------------------------------------------------------------------------- step 3: the four requests

def show_layouts(d: dict) -> None:
    words = len(d["guide"].split())
    rows = [("plain", f"system = [repo guide, {words:,} words]                 + memory from --boundary"),
            ("m5", f"system = [repo guide ◆ {{\"type\": \"ephemeral\"}}]           + memory from --boundary"),
            ("h1", f"system = [repo guide ◆ {{\"type\": \"ephemeral\", \"ttl\": \"1h\"}}] + memory from --boundary"),
            ("ping", f"same as B, plus one 1-token call {PING_AT // 60} min into the break")]
    for a, layout in rows:
        emit("text", f"    {ARM_LABEL[a]:<36} {layout}")
    note("◆ = the cache breakpoint. A cache entry lives 5 minutes by default (1 hour with ttl 1h); every read starts "
         "the clock again")
    note("MemoryRouter attaches the memory after the system prompt, so it never disturbs the cached guide")
    note(f"the question, the memory and the model are the same in all four; only the cache setting differs")
    emit("layouts", "", rows=[dict(arm=a, layout=l) for a, l in rows], guide_words=words, ping_at=PING_AT, gap=BREAK)


# --------------------------------------------------------------------------- steps 4 and 6: a session

def new_run_id() -> str:
    return "%06x" % random.randrange(16 ** 6)


def ask_all(cli: CLI, world: dict, d: dict, run: str, q: dict, p: dict, echo: bool = False) -> list[dict]:
    """One request through A–D at once (each arm has its own guide bytes, so they cannot warm each other)."""
    bodies = {a: body_for(d, run, a, q["text"]) for a in ARMS}

    def one(a: str):
        return a, router(cli, bodies[a], world["boundary"], echo=echo and a == "h1")

    if echo:   # echo one command (C, the one with the ttl), then the rest at once
        res = dict([one("h1")])
        with ThreadPoolExecutor(3) as pool:
            res.update(dict(pool.map(one, ("plain", "m5", "ping"))))
    else:
        with ThreadPoolExecutor(4) as pool:
            res = dict(pool.map(one, ARMS))
    if any(r is None for r in res.values()):
        return []
    rows = []
    for a in ARMS:
        r = res[a]
        ok = bool(re.search(q["expect"], r["reply"], re.I)) if q.get("expect") else None
        rows.append(dict(r, arm=a, question=q["text"], ok=ok, usd=cost(p, r)))
    return rows


def show_request(rows: list[dict], session: int, n: int, total: int, last: dict) -> None:
    r0 = rows[0]
    emit("text", f"\n  S{session}·{n}/{total}  “{r0['question']}”")
    for r in rows:
        mark = "✓" if r["ok"] else ("✗" if r["ok"] is False else "·")
        gap = r["at"] - last[r["arm"]] if r["arm"] in last else None
        why = ""
        if session == 2 and n == 1 and r["arm"] != "plain" and gap is not None:
            idle = f"{int(gap // 60)}:{int(gap % 60):02d} since this arm's last call" + (", its ping" if r["arm"] == "ping" else "")
            why = f"  ← {'read: the entry outlived the break' if r['read'] else 'written again: the entry expired'} ({idle})"
        emit("text", f"    {SHORT[r['arm']]}  written {r['written']:>6,} · read {r['read']:>6,} · uncached {r['fresh']:>5,}  "
                     f"{r['usd'] * 1e6:>6,.0f} USD/1M  {mark}  {' '.join(r['reply'].split())[:46]}{why}")
    emit("request", "", session=session, n=n, total=total, rows=rows)


def run_session(cli: CLI, world: dict, d: dict, p: dict, run: str, session: int, last: dict) -> list[dict]:
    qs = d[f"session{session}"]
    rows: list[dict] = []
    for i, q in enumerate(qs):
        mine = ask_all(cli, world, d, run, q, p, echo=i == 0)
        if not mine:
            return rows
        mine = [dict(r, session=session, n=i + 1) for r in mine]
        show_request(mine, session, i + 1, len(qs), last)
        for r in mine:
            last[r["arm"]] = r["at"]
        rows += mine
    return rows


# --------------------------------------------------------------------------- step 5: the break

def clock(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def take_break(cli: CLI, world: dict, d: dict, p: dict, run: str, last: dict) -> dict:
    """Session 1's decisions go into memory; then nothing happens until every arm has been idle BREAK seconds,
    except D's keep-alive ping at PING_AT."""
    start = max(last.values())
    add_notes(cli, world["project"], d["facts_today"])
    note(f"{len(d['facts_today'])} decisions from this morning's session written to memory — the next session will "
         "need them")
    facts = project_facts(cli, world["project"], echo=True)
    note(f"the project now holds {len(facts)} session notes; MemoryRouter will find them after the break")
    emit("memory", "", facts=[f["fact"] for f in facts])
    ping_due = last["ping"] + PING_AT
    resume = start + BREAK
    ping = None
    note(f"{d['developer']} steps away. Session 2 starts when every arm has been idle {clock(BREAK)}; "
         f"D pings its cache at {clock(PING_AT)}")
    next_tick = time.time()
    while True:
        now = time.time()
        if ping is None and now >= ping_due:
            body = dict(body_for(d, run, "ping", "ping"), max_tokens=1)
            ping = router(cli, body, None, echo=True)
            if ping is None:
                return {}
            ping["usd"] = cost(p, ping)
            last["ping"] = ping["at"]
            emit("text", f"    D  keep-alive ping at {clock(ping['at'] - start)}: written {ping['written']:,} · "
                         f"read {ping['read']:,} · uncached {ping['fresh']:,}  ({ping['usd'] * 1e6:,.0f} USD per 1M pings)")
            emit("ping", "", **ping, offset=ping["at"] - start)
            continue
        if now >= resume:
            break
        if now >= next_tick:
            left = resume - now
            emit("progress", f"    break {clock(now - start)} of {clock(BREAK)}  ·  session 2 in {clock(left)}",
                 elapsed=now - start, total=BREAK)
            next_tick += 30
        time.sleep(min(1.0, max(0.05, resume - time.time())))
    return dict(start=start, seconds=time.time() - start, ping=ping, facts=len(facts))


# --------------------------------------------------------------------------- step 7: the bill

def tally(rows: list[dict], arm: str, extra: list[dict] = ()) -> dict:
    mine = [r for r in rows if r["arm"] == arm] + list(extra)
    out = dict(arm=arm, calls=len(mine), fresh=sum(r["fresh"] for r in mine), written=sum(r["written"] for r in mine),
               read=sum(r["read"] for r in mine), writes=sum(1 for r in mine if r["written"]),
               reads=sum(1 for r in mine if r["read"]), usd=sum(r["usd"] for r in mine),
               right=sum(1 for r in rows if r["arm"] == arm and r["ok"]),
               judged=sum(1 for r in rows if r["arm"] == arm and r["ok"] is not None))
    for s in (1, 2):
        out[f"s{s}"] = sum(r["usd"] for r in rows if r["arm"] == arm and r["session"] == s)
    out["break"] = sum(r["usd"] for r in extra)
    first = next((r for r in rows if r["arm"] == arm and r["session"] == 2 and r["n"] == 1), None)
    out["s2_first"] = first and dict(written=first["written"], read=first["read"], usd=first["usd"])
    return out


def show_bill(rows: list[dict], brk: dict, p: dict) -> dict:
    pings = [brk["ping"]] if brk.get("ping") else []
    bill = {a: tally(rows, a, pings if a == "ping" else ()) for a in ARMS}
    emit("text", f"    {'':36} {'session 1':>10} {'break':>8} {'session 2':>10} {'total USD':>10} {'vs A':>6}  "
                 f"{'S2 first call':<22} {'right':>6}")
    A = bill["plain"]
    for a in ARMS:
        b = bill[a]
        f = b["s2_first"] or {}
        first = "—" if a == "plain" else ("read " if f.get("read") else "WRITE ") + f"{(f.get('read') or f.get('written') or 0):,}"
        emit("text", f"    {ARM_LABEL[a]:<36} {b['s1']:>10.5f} {b['break']:>8.5f} {b['s2']:>10.5f} {b['usd']:>10.5f} "
                     f"{(b['usd'] / A['usd'] if A['usd'] else 0):>5.2f}×  {first:<22} {b['right']}/{b['judged']}")
    B, C, D = bill["m5"], bill["h1"], bill["ping"]
    if B["s2_first"] and not B["s2_first"]["read"]:
        note(f"B's 5-minute entry expired during the {clock(brk['seconds'])} break: session 2 opened by writing the whole "
             f"guide again ({B['s2_first']['written']:,} tokens at the write price)")
    elif B["s2_first"]:
        note("B's entry was still there after the break on this run")
    if C["s2_first"] and C["s2_first"]["read"]:
        note(f"C paid the 1-hour write price once ({p['write1h'] / p['input']:.2f}× input) and read the guide across the break")
    if D["s2_first"] and D["s2_first"]["read"] and pings:
        note(f"D's one ping ({pings[0]['read']:,} tokens read, {pings[0]['usd'] * 1e6:,.0f} USD per 1M) started the 5-minute "
             "clock again, so the entry was still there when session 2 began")
    rights = {SHORT[a]: f"{bill[a]['right']}/{bill[a]['judged']}" for a in ARMS}
    note("answers from memory: " + " · ".join(f"{k} {v}" for k, v in rights.items())
         + " — same question, same memory in every arm; the cache setting changes the bill, not the answer")
    emit("bill", "", bill=bill, prices=p, gap=brk.get("seconds"))
    return bill


def show_breakeven(p: dict, tokens: int, n1: int, n2: int) -> list[dict]:
    """This demo's traffic (n1 requests, a break, n2 requests) with breaks of other lengths, computed from the price
    list for the guide tokens only (not measured): A sends the guide uncached every time; B writes it at the 5-minute
    price and writes it again after a break of 5 minutes or more; C writes it at the 1-hour price and reads it across
    any break under an hour; D is B plus one read-priced ping every 4 minutes of the break."""
    rows = []
    for mins in (3, 5.5, 20, 45, 90):
        r, w5, w1 = p["read"], p["write5"], p["write1h"]
        reads = (n1 - 1 + n2 - 1) * r
        pings = int(mins // 4) if mins >= 5 else 0
        units = dict(plain=(n1 + n2) * p["input"],
                     m5=w5 + reads + (r if mins < 5 else w5),
                     h1=w1 + reads + (r if mins < 60 else w1),
                     ping=w5 + reads + pings * r + r)
        rows.append(dict(minutes=mins, pings=pings, **{k: v * tokens / 1e6 for k, v in units.items()}))
    emit("text", f"    {'break':>8} {'A':>9} {'B 5 min':>9} {'C 1 h':>9} {'D ping':>9}            "
                 f"USD for the guide over {n1} requests, the break, {n2} requests ({tokens:,} tokens)")
    for r in rows:
        best = min(ARMS, key=lambda k: r[k])
        emit("text", f"    {r['minutes']:>6g} m {r['plain']:>9.5f} {r['m5']:>9.5f} {r['h1']:>9.5f} {r['ping']:>9.5f} "
                     f"({r['pings']:>2} ping{'s' if r['pings'] != 1 else ' '})   cheapest: {SHORT[best]}")
    # D beats C while its pings cost less than C's extra on the first write: fewer than (write1h - write5) / read pings
    limit = 4 * math.ceil((p["write1h"] - p["write5"]) / p["read"]) if p["read"] else 0
    note("computed from the prices above for the guide tokens only, not measured: under 5 minutes the default is enough; "
         f"for breaks under about {limit} minutes D's pings cost less than C's 1-hour write; longer than that, C; past an "
         "hour C's entry is gone too, and its write costs more than B's")
    emit("breakeven", "", rows=rows, tokens=tokens, n1=n1, n2=n2, limit=limit)
    return rows


TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, f"Set up — yesterday's session notes, one boundary, the price list")
    world = setup(cli)
    p = prices(cli)
    show_prices(p)

    banner(3, TOTAL, f"Four ways to cache {d['assistant']}'s repo guide")
    show_layouts(d)

    run = new_run_id()
    last: dict = {}
    banner(4, TOTAL, f"Session 1 — {d['developer']}'s morning with {d['assistant']}, each request through A–D")
    note(f"{MODEL}, temperature 0, MemoryRouter memory on every arm (`--boundary`)")
    note(f"run {run}: each arm's guide starts with “Reference copy {run}-<arm>.”, so no arm reads what another arm "
         "or an earlier run wrote")
    rows = run_session(cli, world, d, p, run, 1, last)

    brk: dict = {}
    if rows:
        banner(5, TOTAL, f"The break — {clock(BREAK)} away from the keyboard")
        brk = take_break(cli, world, d, p, run, last)

    if brk:
        banner(6, TOTAL, "Session 2 — back after the break")
        rows += run_session(cli, world, d, p, run, 2, last)

    bill, even = {}, []
    if brk and len(rows) == 4 * (len(d["session1"]) + len(d["session2"])):
        banner(7, TOTAL, "The bill — same answers, four cache settings")
        bill = show_bill(rows, brk, p)
        tokens = next((r["written"] or r["read"] for r in rows if r["arm"] == "h1" and (r["written"] or r["read"])), 0)
        if tokens:
            emit("text", "")
            even = show_breakeven(p, tokens, len(d["session1"]), len(d["session2"]))

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(world=world, prices=p, run=run, requests=rows, brk=brk, bill=bill, breakeven=even, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every call, token count and reply: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def ask(cli: CLI, question: str) -> dict:
    """Any question to Kit, with the team's memory (`--boundary`) and the guide cached for an hour (arm C)."""
    d = load_data()
    world = load_world(cli)
    p = prices(cli, echo=False)
    body = body_for(d, "ask", "h1", question)
    r = router(cli, body, world["boundary"], echo=True)
    if not r:
        die("MemoryRouter is not available on this account (no balance)")
    r["usd"] = cost(p, r)
    emit("text", f"\n    written {r['written']:,} · read {r['read']:,} · uncached {r['fresh']:,}  "
                 f"({r['usd'] * 1e6:,.0f} USD per 1M calls)")
    emit("text", wrap(r["reply"], "    | ", 100))
    note("ask again within the hour and the guide is read from the cache (its first line is the same for every ask)")
    emit("asked", "", question=question, row=r)
    return r


def show_facts(cli: CLI) -> None:
    """What Kit remembers about the repository (`fact list`)."""
    world = load_world(cli)
    facts = project_facts(cli, world["project"], echo=True)
    for f in sorted(facts, key=lambda f: f["fact"]):
        emit("text", "\n".join(textwrap.wrap(f["fact"], 100, initial_indent="    - ", subsequent_indent="      ")))
    emit("facts_view", "", n=len(facts))


# --------------------------------------------------------------------------- cleanup

def delete_fact(cli: CLI, fact_id: str, scope: list[str]) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def archive_report() -> None:
    """The last run's report moves to out/runs/ (never deleted), so a new run or a cleanup cannot overwrite it."""
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    nfacts = nproj = 0
    p = find_project(cli, echo=not quiet)
    if p:
        left = project_facts(cli, p["id"])
        for attempt in range(4):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], ["--project", p["id"]]), left))
            nfacts += len(left)
            left = project_facts(cli, p["id"])
            if not left:
                break
            time.sleep(2 * (attempt + 1))
        if left:
            die(f"{len(left)} fact(s) are still in {p['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
        nproj = 1
    for f in list(OUT.glob("body-*.json")) if OUT.exists() else []:
        f.unlink()
    if not quiet:
        note(f"deleted {len(bs)} boundary, {nfacts} facts and {nproj} project" if (bs or nproj) else "nothing to delete")
    archive_report()


EXAMPLE = '"Can I store sessions in Redis for the auth refactor?"'


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = what Kit remembers about the repo; "
                         "ask \"question\" = one question to Kit with its memory; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: the question")
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
    if args.command == "facts":
        banner(2, 2, "What Kit remembers about the repository")
        show_facts(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask \"question\"   (e.g. python3 demo.py ask {EXAMPLE})")
        banner(2, 2, "One question to Kit, with the team's memory")
        ask(cli, args.args[0])
        return
    archive_report()
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py ask {EXAMPLE}` or `python3 demo.py facts`; "
          "`python3 demo.py cleanup` removes the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        # The failing command can be `auth login --api-key …`: never print the key.
        shown_cmd = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-…", shlex.join(e.cmd)) if e.cmd else "(memorylake)"
        print(f"\ncommand failed: {shown_cmd}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

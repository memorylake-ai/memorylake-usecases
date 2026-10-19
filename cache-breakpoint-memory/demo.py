#!/usr/bin/env python3
"""cache-breakpoint-memory — a Claude API app caches its long playbook with `cache_control` and adds what it remembers
about the customer to every request. Where that memory goes decides whether the cache is ever read.

Larkspan Home (a fictional homewares retailer) runs a returns assistant, Wren, on claude-haiku-4-5. Every request
carries the same returns playbook (about 7,000 tokens) plus what Wren remembers about the customer in question.
Twelve questions from six customers go through four ways of building the Anthropic Messages request:

  A  no cache_control; the customer's facts (`memorylake search` top-5) as a second system block
  B  cache_control on the playbook block; the same facts as a second system block after it
  C  cache_control on the playbook block; the same facts at the top of the user turn
  D  cache_control on the playbook block; MemoryRouter attaches the memory itself (`router messages --boundary`)

B is the layout Anthropic's prompt-caching guide describes: the stable block first, the breakpoint on it, the part
that changes after it. Sent through MemoryRouter (`router messages`), every B request reports the whole system
prompt as `cache_creation_input_tokens` and nothing as `cache_read_input_tokens`: the breakpoint ends up covering the
memory block too, so a new memory block means a new cache entry, written at the cache-write price — more than not
caching at all (A). C and D send the playbook as the only system block and read it from the cache from the second
request on. Prices come from the account's own `router models` list.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router calls
are billed to your MemoryLake balance (64 calls on claude-haiku-4-5 per run, about 0.25 USD).
"""
from __future__ import annotations

import argparse
import hashlib
import random
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cbp"  # memorylake-usecases / cache-breakpoint-memory


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
TOP_K = 5
ARMS = ("plain", "system", "user", "router")
SHORT = {"plain": "A", "system": "B", "user": "C", "router": "D"}
ARM_LABEL = {"plain": "A  no cache_control",
             "system": "B  cached block + memory block",
             "user": "C  cached block, memory in user turn",
             "router": "D  cached block + --boundary"}
MEM_HEAD = "What you remember about this customer:"


def load_data() -> dict:
    return json.loads((DATA / "larkspan.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return "\n".join(textwrap.wrap(" ".join(text.split()), width, initial_indent=indent, subsequent_indent=indent)) or indent


def customer(d: dict, key: str) -> dict:
    return next(c for c in d["customers"] if c["key"] == key)


def who_in(d: dict, text: str) -> dict | None:
    """The customer a free-form question is about: first or last name, case-insensitive."""
    low = text.lower()
    for c in d["customers"]:
        if any(part.lower() in low for part in c["name"].split()):
            return c
    return None


def memory_block(facts: list[str]) -> str:
    return MEM_HEAD + "\n" + "\n".join("- " + f for f in facts) if facts else MEM_HEAD + "\n(nothing found)"


def playbook(d: dict, run: str, arm: str, short: bool = False) -> str:
    """The playbook with a first line naming this run and arm. Every arm of every run starts from its own bytes, so
    no arm reads a cache entry another arm (or an earlier run of this demo) wrote."""
    return f"Reference copy {run}-{SHORT.get(arm, arm)}.\n" + d["playbook_short" if short else "playbook"]


def body_for(d: dict, run: str, arm: str, question: str, facts: list[str]) -> dict:
    """The Anthropic Messages request each arm sends (MemoryRouter adds D's memory on its side)."""
    pb = {"type": "text", "text": playbook(d, run, arm)}
    mem = {"type": "text", "text": memory_block(facts)}
    cached = dict(pb, cache_control={"type": "ephemeral"})
    user = {"role": "user", "content": question}
    if arm == "plain":
        system, messages = [pb, mem], [user]
    elif arm == "system":
        system, messages = [cached, mem], [user]
    elif arm == "user":
        system, messages = [cached], [{"role": "user", "content": memory_block(facts) + "\n\nQuestion: " + question}]
    else:
        system, messages = [cached], [user]
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "temperature": 0, "system": system, "messages": messages}


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
    """USD per million tokens for MODEL, from the account's own price list: input, cache write (5 min), cache read,
    output."""
    res = cli.run("router", "models", echo=echo) or {}
    items = (res.get("data") or {}).get("items") or res.get("items") or []
    m = next((i for i in items if i.get("name") == MODEL), None)
    if not m:
        die(f"{MODEL} is not in `memorylake router models` on this account")
    p = m.get("price") or {}
    det = p.get("details") or {}

    def usd(node) -> float | None:
        return node.get("usd_per_million") if isinstance(node, dict) else None

    out = dict(input=usd(p.get("input")), output=usd(p.get("output")),
               write=usd(det.get("cache_write_5m")), read=usd(det.get("cache_read")))
    if out["input"] is None or out["output"] is None:
        die(f"no input/output price for {MODEL} in `router models`")
    for k in ("write", "read"):
        if out[k] is None:
            out[k] = out["input"]
            note(f"{MODEL} lists no cache_{k} price; those tokens are priced as input")
    return out


def cost(p: dict, fresh: int, written: int, read: int, completion: int) -> float:
    return (fresh * p["input"] + written * p["write"] + read * p["read"] + completion * p["output"]) / 1e6


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, body: dict, boundary_id: str | None = None, max_tokens: int | None = None,
           echo: bool = False) -> dict | None:
    """One `router messages --body` call. The body is a whole Anthropic Messages request (system blocks with
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
    with LOCK:
        cli.tokens["prompt"] += fresh + written + read  # type: ignore[attr-defined]
        cli.tokens["completion"] += out_t  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    reply = " ".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text").strip()
    return dict(reply=reply, fresh=fresh, written=written, read=read, completion_tokens=out_t,
                reported="cache_creation_input_tokens" in u)


def retrieve(cli: CLI, world: dict, q: str, echo: bool = False) -> list[str]:
    res = cli.run("search", "--projects", world["project"], "--types", "fact", "--top-k", str(TOP_K), q,
                  scoped=True, echo=echo) or {}
    return [f["fact"] for f in res.get("facts") or []]


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if find_project(cli) or demo_boundaries(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    p = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("memory"),
                "--description", f"{d['org']} customer memory for the returns assistant. Demo data.", scoped=True)
    world = {"project": p["id"]}
    for n, c in enumerate(d["customers"]):
        cli.run("fact", "add", *c["facts"], "--project", world["project"], scoped=True, echo=n == 0)
    total = sum(len(c["facts"]) for c in d["customers"])
    note(f"{total} facts pinned verbatim, {len(d['customers'][0]['facts'])} per customer: "
         + " · ".join(c["name"] for c in d["customers"]))
    b = cli.run("boundary", "create", "--name", f"{d['assistant']} customer memory", "--custom-id", cid("customers"),
                "--projects", world["project"], scoped=True)
    world["boundary"] = b["id"]
    note("one boundary over the project — what arm D's MemoryRouter calls read")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    p = find_project(cli)
    bs = demo_boundaries(cli)
    if not p or not bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return {"project": p["id"], "boundary": bs[0]["id"]}


def show_prices(p: dict) -> None:
    emit("text", f"    {MODEL}: input {p['input']} · cache write (5 min) {p['write']} · cache read {p['read']} · "
                 f"output {p['output']} USD per million tokens")
    if p["input"]:
        note(f"a prompt token written to the cache costs {p['write'] / p['input']:.2f}× a plain one; read back from it, "
             f"{p['read'] / p['input']:.2f}×")
    emit("prices", "", model=MODEL, **p)


# --------------------------------------------------------------------------- step 3: the four requests

def show_layouts(d: dict) -> None:
    pb_words = len(d["playbook"].split())
    rows = [("plain", f"system = [playbook, {pb_words} words] [memory: top-{TOP_K} facts]      user = question"),
            ("system", f"system = [playbook, {pb_words} words ◆] [memory: top-{TOP_K} facts]    user = question"),
            ("user", f"system = [playbook, {pb_words} words ◆]      user = [memory: top-{TOP_K} facts] + question"),
            ("router", f"system = [playbook, {pb_words} words ◆]      user = question   … MemoryRouter attaches memory")]
    for a, layout in rows:
        emit("text", f"    {ARM_LABEL[a]:<38} {layout}")
    note("◆ = \"cache_control\": {\"type\": \"ephemeral\"} on that block — the cache breakpoint")
    note("Anthropic bills a prompt in three parts: tokens written to the cache (cache_creation_input_tokens), "
         "tokens read from it (cache_read_input_tokens) and the rest (input_tokens)")
    note("B is the layout the caching guide describes: stable block first, breakpoint on it, the changing part after it")
    note("A, B and C carry the same memory block, from one `search` per question; D lets MemoryRouter pick")
    emit("layouts", "", rows=[dict(arm=a, layout=l) for a, l in rows], playbook_words=pb_words)


# --------------------------------------------------------------------------- step 4: the traffic

def new_run_id() -> str:
    return "%06x" % random.randrange(16 ** 6)


def ask_one(cli: CLI, world: dict, d: dict, run: str, qi: int, q: dict | None, text: str, p: dict, sent: dict,
            echo: bool = False) -> list[dict]:
    """One question through A–D (in parallel: each arm has its own playbook bytes, so they cannot warm each other).
    `sent` remembers each arm's system prompts this run, to tell a read that repeats an earlier request apart."""
    c = customer(d, q["customer"]) if q else who_in(d, text)
    facts = retrieve(cli, world, text, echo=echo)
    bodies = {a: body_for(d, run, a, text, facts) for a in ARMS}

    def one(a: str):
        return a, router(cli, bodies[a], world["boundary"] if a == "router" else None,
                         echo=echo and a in ("system", "router"))

    if echo:   # the first question: echo two commands in a fixed order, then the rest at once
        res = dict(one(a) for a in ("system", "router"))
        with ThreadPoolExecutor(2) as pool:
            res.update(dict(pool.map(one, ("plain", "user"))))
    else:
        with ThreadPoolExecutor(4) as pool:
            res = dict(pool.map(one, ARMS))
    if any(r is None for r in res.values()):
        return []
    rows = []
    for a in ARMS:
        r = res[a]
        key = json.dumps(bodies[a]["system"], sort_keys=True)
        repeat = key in sent.setdefault(a, set())
        sent[a].add(key)
        ok = bool(re.search(q["expect"], r["reply"], re.I)) if q else None
        rows.append(dict(q=qi, question=text, customer=c and c["key"], arm=a, ok=ok, reply=r["reply"],
                         fresh=r["fresh"], written=r["written"], read=r["read"], completion_tokens=r["completion_tokens"],
                         reported=r["reported"], repeat=repeat,
                         usd=cost(p, r["fresh"], r["written"], r["read"], r["completion_tokens"]), facts=facts))
    # The same call as D without the boundary: if D sent no more tokens than this, MemoryRouter attached nothing (∅).
    # It runs after the four arms and is not part of the bill.
    base = router(cli, bodies["router"], None, max_tokens=1)
    for r in rows:
        if r["arm"] == "router" and base:
            r["empty"] = r["fresh"] + r["written"] + r["read"] <= base["fresh"] + base["written"] + base["read"]
    return rows


def show_question(rows: list[dict], n: int, total: int) -> None:
    r0 = rows[0]
    emit("text", f"\n  Q{n}/{total}  “{r0['question']}”")
    for r in rows:
        mark = "∅" if r.get("empty") else ("✓" if r["ok"] else ("✗" if r["ok"] is False else " "))
        # B can only read when its whole system prompt, memory block included, repeats an earlier request
        rep = "  (whole system prompt repeats an earlier question)" if r["arm"] == "system" and r["repeat"] and r["read"] else ""
        emit("text", f"    {SHORT[r['arm']]}  written {r['written']:>5,} · read {r['read']:>5,} · uncached {r['fresh']:>5,}  "
                     f"{r['usd'] * 1e6:>6,.0f} USD/1M  {mark}  {' '.join(r['reply'].split())[:52]}{rep}")
    emit("question", "", n=n, total=total, rows=rows)


def run_traffic(cli: CLI, world: dict, d: dict, p: dict, run: str) -> list[dict]:
    rows: list[dict] = []
    sent: dict = {}
    qs = d["questions"]
    for i, q in enumerate(qs):
        mine = ask_one(cli, world, d, run, i, q, q["text"], p, sent, echo=i == 0)
        if not mine:
            return rows
        show_question(mine, i + 1, len(qs))
        rows += mine
    return rows


# --------------------------------------------------------------------------- step 5: the bill

def tally(rows: list[dict], arm: str) -> dict:
    mine = [r for r in rows if r["arm"] == arm]
    return dict(arm=arm, calls=len(mine),
                fresh=sum(r["fresh"] for r in mine), written=sum(r["written"] for r in mine),
                read=sum(r["read"] for r in mine),
                writes=sum(1 for r in mine if r["written"]), reads=sum(1 for r in mine if r["read"]),
                repeat_reads=sum(1 for r in mine if r["read"] and r["repeat"]),
                right=sum(1 for r in mine if r["ok"] and not r.get("empty")),
                empty=sum(1 for r in mine if r.get("empty")),
                unreported=sum(1 for r in mine if not r["reported"]),
                usd=sum(r["usd"] for r in mine))


def show_bill(rows: list[dict], p: dict) -> dict:
    bill = {a: tally(rows, a) for a in ARMS}
    emit("text", f"    {'':38} {'written':>8} {'read':>8} {'uncached':>9} {'writes':>7} {'reads':>6} {'right':>6} "
                 f"{'USD, these calls':>17} {'USD per 1M calls':>17}")
    for a in ARMS:
        b = bill[a]
        right = f"{b['right']}/{b['calls'] - b['empty']}" + (f" ∅{b['empty']}" if b["empty"] else "")
        emit("text", f"    {ARM_LABEL[a]:<38} {b['written']:>8,} {b['read']:>8,} {b['fresh']:>9,} {b['writes']:>3}/{b['calls']:<3} "
                     f"{b['reads']:>2}/{b['calls']:<3} {right:>6} {b['usd']:>17.6f} {b['usd'] / max(b['calls'], 1) * 1e6:>17,.0f}")
    if any(b["unreported"] for b in bill.values()):
        note("calls whose reply did not report cache_creation_input_tokens are counted as 0 written / 0 read")
    A, B, C, D = (bill[a] for a in ARMS)
    if A["usd"] and B["calls"]:
        note(f"B paid {B['usd'] / A['usd']:.2f}× what A paid for the same {B['calls']} requests: it wrote the cache "
             f"{B['writes']} time(s) and read it {B['reads']} time(s)"
             + (f" ({B['repeat_reads']} of them when the whole system prompt, memory included, repeated an earlier one)"
                if B["repeat_reads"] else ""))
    for k in (C, D):
        if A["usd"] and k["calls"]:
            note(f"{SHORT[k['arm']]} paid {k['usd'] / A['usd']:.2f}× A: wrote the playbook {k['writes']} time(s), "
                 f"read it {k['reads']} time(s)")
    same = A["right"] == B["right"] == C["right"]
    note(f"right answers: A {A['right']} · B {B['right']} · C {C['right']} · D {D['right']} of {A['calls']}"
         + (f" (D: MemoryRouter attached nothing {D['empty']} time(s), not counted)" if D["empty"] else "")
         + (" — A, B and C sent the same memory block and answered alike; only the bill differs" if same else ""))
    missed = [r for r in rows if r["arm"] == "router" and r["ok"] is False and not r.get("empty")]
    if missed:
        note(f"D missed {len(missed)} (Q{', Q'.join(str(r['q'] + 1) for r in missed)}): MemoryRouter attached memory "
             f"({', '.join(str(r['fresh']) for r in missed)} uncached tokens) and the answer still missed — which facts it "
             "attached is not in the reply; A–C got `search` top-5. Not a caching effect: D's breakpoint read the playbook")
    emit("bill", "", bill=bill, prices=p)
    return bill


# --------------------------------------------------------------------------- step 6: too short to cache

def too_short(cli: CLI, d: dict, run: str, p: dict) -> dict:
    """The trimmed playbook (the first sections only) with the same breakpoint, sent twice in a row."""
    q = d["questions"][0]["text"]
    out = {}
    for name, short in (("full", False), ("trimmed", True)):
        body = {"model": MODEL, "max_tokens": 1, "temperature": 0,
                "system": [{"type": "text", "text": playbook(d, run, "S" + name[0].upper(), short),
                            "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": q}]}
        calls = []
        for i in (1, 2):
            r = router(cli, body, echo=i == 1 and short)
            if not r:
                return {}
            calls.append(r)
            emit("text", f"    {name:<8} playbook ({len(d['playbook_short' if short else 'playbook'].split()):,} words), call {i}: "
                         f"written {r['written']:>5,} · read {r['read']:>5,} · uncached {r['fresh']:>5,}")
        out[name] = calls
    t = out["trimmed"]
    total = t[0]["fresh"] + t[0]["written"] + t[0]["read"]
    if not any(c["written"] or c["read"] for c in t):
        note(f"the trimmed playbook ({total:,} prompt tokens) carried the same breakpoint and was never written or read: "
             f"below the model's minimum cacheable length, `cache_control` is ignored and nothing says so")
    else:
        note(f"the trimmed playbook ({total:,} prompt tokens) was cached too on this run")
    emit("short", "", **out)
    return out


TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, f"Set up — {len(d['customers'])} customers' memory, one boundary, the price list")
    world = setup(cli)
    p = prices(cli)
    show_prices(p)

    banner(3, TOTAL, f"Four ways to build {d['assistant']}'s request")
    show_layouts(d)

    run = new_run_id()
    banner(4, TOTAL, f"Traffic — {len(d['questions'])} questions from {len(d['customers'])} customers, each through A–D")
    note(f"{MODEL}, temperature 0; one `search` per question feeds A, B and C, so their memory block is identical")
    note(f"run {run}: each arm's playbook starts with “Reference copy {run}-<arm>.”, so no arm reads what another arm "
         "or an earlier run wrote")
    rows = run_traffic(cli, world, d, p, run)

    banner(5, TOTAL, "The bill — same memory, same answers, four prices")
    bill = show_bill(rows, p) if rows else {}

    banner(6, TOTAL, "Too short to cache — the same breakpoint on a trimmed playbook")
    short = too_short(cli, d, run, p) if cli.router_ok else {}  # type: ignore[attr-defined]

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(world=world, prices=p, run=run, answers=rows, bill=bill, short=short, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every call, token count and reply: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def ask(cli: CLI, what: str) -> list[dict]:
    """A question number (1–12) or any question that names a customer, through A–D; then the customer's other
    question (or the same one again), the way traffic arrives: a moment later, a different memory block."""
    d = load_data()
    world = load_world(cli)
    p = prices(cli, echo=False)
    qs = d["questions"]
    q = qs[int(what) - 1] if what.strip().isdigit() and 1 <= int(what) <= len(qs) else None
    text = q["text"] if q else what.strip()
    c = customer(d, q["customer"]) if q else who_in(d, text)
    second = next((x for x in qs if c and x["customer"] == c["key"] and x["text"] != text), q)
    run, sent, rows = new_run_id(), {}, []
    emit("text", f"  run {run}" + (f" · customer: {c['name']}" if c else " · no customer named"))
    for rnd, (qq, tt) in enumerate(((q, text), (second, second["text"] if second else text)), 1):
        mine = ask_one(cli, world, d, run, rnd - 1, qq, tt, p, sent, echo=rnd == 1)
        if not mine:
            die("MemoryRouter is not available on this account (no balance)")
        emit("text", f"\n  round {rnd}  “{tt}”")
        for r in mine:
            mark = "∅" if r.get("empty") else ("✓" if r["ok"] else ("✗" if r["ok"] is False else " "))
            emit("text", f"    {ARM_LABEL[r['arm']]:<38} written {r['written']:>5,} · read {r['read']:>5,} · "
                         f"uncached {r['fresh']:>5,}  {r['usd'] * 1e6:>6,.0f} USD/1M  {mark}")
            emit("text", wrap(r["reply"], "      | ", 100))
        rows += [dict(r, round=rnd) for r in mine]
    b2 = next((r for r in rows if r["arm"] == "system" and r["round"] == 2), None)
    if b2 and not b2["read"]:
        note("round 2: C and D read the playbook written in round 1; B wrote its whole system prompt again, because its "
             "memory block changed")
    elif b2 and b2["repeat"]:
        note("round 2: B read its cache because its whole system prompt, memory block included, repeated round 1")
    emit("asked", "", question=text, rows=rows)
    return rows


def show_facts(cli: CLI) -> None:
    """What the project remembers, per customer (`fact list`)."""
    d = load_data()
    world = load_world(cli)
    facts = project_facts(cli, world["project"], echo=True)
    for c in d["customers"]:
        mine = [f["fact"] for f in facts if f["fact"].startswith(c["name"])]
        emit("text", f"\n  {c['name']} — {len(mine)} facts")
        for f in mine:
            emit("text", wrap(f, "    - ", 100))
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


EXAMPLE = "4"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = what the project remembers per customer; "
                         "ask <1-12 | \"question\"> = one question through A–D, then the customer's other one; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question number (1-12) or a question that names a customer")
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
        banner(2, 2, "What the project remembers, per customer")
        show_facts(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask <1-12 | "question">   (e.g. python3 demo.py ask {EXAMPLE})')
        banner(2, 2, "One question through A–D, then the next one for the same customer")
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

#!/usr/bin/env python3
"""prompt-cache-memory — the same memory, the same answers, two different bills: where the memory block sits in the
prompt decides whether the provider's prompt cache ever hits.

Tessavane Freight (a fictional carrier) runs a support assistant, Dock. Every request carries the same handling
playbook (about 1,300 tokens, shared by all customers) plus what Dock remembers about the customer who is asking.
Twelve questions from six customers go through four ways of building the prompt:

  A  the whole ticket history pasted in front of the playbook (the "stuff everything" baseline)
  B  `memorylake search` top-5 facts, placed in front of the playbook
  C  the same top-5 facts, placed after the playbook
  D  the playbook alone, with MemoryRouter attaching memory (`router chat --boundary`)

B and C send the same tokens and get the same answers. Only `usage.prompt_tokens_details.cached_tokens` differs: a
block that changes with every question, placed first, means no request ever shares a prefix with the one before, so
the provider never reads the playbook from its cache. Prices come from the account's own `router models` list.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router calls
are billed to your MemoryLake balance (60 calls on gpt-4o-mini per run, under 0.02 USD).
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

PREFIX = "mlu-pcc"  # memorylake-usecases / prompt-cache-memory


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

MODEL = "gpt-4o-mini"
MAX_TOKENS = 80
TOP_K = 5
ARMS = ("stuffed", "front", "back", "router")
SHORT = {"stuffed": "A", "front": "B", "back": "C", "router": "D"}
ARM_LABEL = {"stuffed": "A  history pasted in front", "front": "B  retrieved, in front",
             "back": "C  retrieved, after the playbook", "router": "D  MemoryRouter --boundary"}
MEM_HEAD = "What you remember about this customer:"
HEAD_CHARS = 4000   # about the first 1,000 tokens: OpenAI caches nothing shorter than 1,024 tokens
SEEN_WINDOW = 3600  # providers keep a cached prefix for minutes, at most about an hour
SENT = OUT / "sent.json"


def load_data() -> dict:
    return json.loads((DATA / "freight.json").read_text(encoding="utf-8"))


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


def system_for(d: dict, arm: str, c: dict | None, facts: list[str]) -> str:
    pb = d["playbook"]
    if arm == "stuffed":
        if not c:
            return pb
        return f"Customer file for {c['name']} ({c['company']}), every past ticket:\n{c['history']}\n\n{pb}"
    if arm == "front":
        return memory_block(facts) + "\n\n" + pb
    if arm == "back":
        return pb + "\n\n" + memory_block(facts)
    return pb


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
    """USD per million tokens for MODEL, from the account's own price list: input, cache_read, output."""
    res = cli.run("router", "models", echo=echo) or {}
    # CLI v20261023+ prints the model list itself; earlier versions wrapped it in {"data": {"items": [...]}}
    items = res if isinstance(res, list) else (res.get("data") or {}).get("items") or res.get("items") or []
    m = next((i for i in items if i.get("name") == MODEL), None)
    if not m:
        die(f"{MODEL} is not in `memorylake router models` on this account")
    p = m.get("price") or {}

    def usd(node) -> float | None:
        return node.get("usd_per_million") if isinstance(node, dict) else None

    out = dict(input=usd(p.get("input")), output=usd(p.get("output")),
               cache_read=usd((p.get("details") or {}).get("cache_read")))
    if out["input"] is None or out["output"] is None:
        die(f"no input/output price for {MODEL} in `router models`")
    if out["cache_read"] is None:
        out["cache_read"] = out["input"]  # no cache discount listed: cached tokens cost the same
        note(f"{MODEL} lists no cache_read price; cached tokens are priced as input")
    return out


def cost(p: dict, prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * p["input"] + cached * p["cache_read"] + completion * p["output"]) / 1e6


# --------------------------------------------------------------------------- what was sent before

def head_key(system: str) -> str:
    return hashlib.sha256(system[:HEAD_CHARS].encode("utf-8")).hexdigest()[:16]


def head_seen(system: str) -> float | None:
    """Minutes since a prompt starting with these same ~1,000 tokens was last sent from this folder, or None.

    Kept locally (out/sent.json), so a hit can be explained: the provider can only reuse a prefix it saw recently."""
    try:
        sent = json.loads(SENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    t = sent.get(head_key(system))
    return (time.time() - t) / 60 if t and time.time() - t < SEEN_WINDOW else None


def recent_questions() -> set[str]:
    """Questions sent from this folder in the last hour (kept in out/sent.json next to the prompt heads)."""
    try:
        sent = json.loads(SENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {k[2:] for k, t in sent.items() if k.startswith("q:") and time.time() - t < SEEN_WINDOW}


def question_sent(text: str) -> None:
    head_sent(None, key="q:" + text)


def head_sent(system: str | None, key: str | None = None) -> None:
    with LOCK:
        try:
            sent = json.loads(SENT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sent = {}
        now = time.time()
        sent = {k: v for k, v in sent.items() if now - v < SEEN_WINDOW}
        sent[key or head_key(system)] = now
        OUT.mkdir(exist_ok=True)
        tmp = SENT.with_suffix(".tmp")
        tmp.write_text(json.dumps(sent), encoding="utf-8")
        os.replace(tmp, SENT)


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, system: str, prompt: str, boundary_id: str | None = None, max_tokens: int = MAX_TOKENS,
           echo: bool = False) -> dict | None:
    """One `router chat --body` call: the system prompt goes in the body (it is long), the question is the PROMPT."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    OUT.mkdir(exist_ok=True)
    with LOCK:
        cli.nbody = getattr(cli, "nbody", 0) + 1  # type: ignore[attr-defined]
        path = OUT / f"body-{cli.nbody % 8}.json"  # type: ignore[attr-defined]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": MODEL, "messages": [{"role": "system", "content": system}]}), encoding="utf-8")
    os.replace(tmp, path)
    args = ["chat", "--body", str(path.relative_to(HERE)) if echo else str(path), "--model", MODEL,
            "--max-tokens", str(max_tokens), "--temperature", "0"]
    if boundary_id:
        args += ["--boundary", boundary_id]
    args.append(prompt)
    seen = head_seen(system)
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
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    head_sent(system)
    u = res.get("usage") or {}
    details = u.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    with LOCK:
        cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
        cli.tokens["completion"] += ct  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=(res["choices"][0]["message"]["content"] or "").strip(), prompt_tokens=pt,
                cached=cached, completion_tokens=ct, seen=None if seen is None else round(seen, 1))


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
                "--description", f"{d['org']} customer memory for the support assistant. Demo data.", scoped=True)
    world = {"project": p["id"]}
    for n, c in enumerate(d["customers"]):
        cli.run("fact", "add", *c["facts"], "--project", world["project"], scoped=True, echo=n == 0)
    total = sum(len(c["facts"]) for c in d["customers"])
    note(f"{total} facts pinned verbatim, {len(d['customers'][0]['facts'])} per customer: "
         + " · ".join(f"{c['name']} ({c['company']})" for c in d["customers"]))
    b = cli.run("boundary", "create", "--name", f"{d['assistant']} support memory", "--custom-id", cid("support"),
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
    emit("text", f"    {MODEL}: input {p['input']} · cache_read {p['cache_read']} · output {p['output']} USD per million tokens")
    if p["input"]:
        note(f"a prompt token the provider reads from its cache costs {p['cache_read'] / p['input']:.0%} of a fresh one")
    emit("prices", "", model=MODEL, **p)


# --------------------------------------------------------------------------- step 3: the four prompts

def show_layouts(d: dict) -> None:
    c = customer(d, d["questions"][0]["customer"])
    pb_words = len(d["playbook"].split())
    hist_words = len(c["history"].split())
    rows = [("stuffed", f"[customer file: {len(c['facts'])} past tickets, ~{hist_words} words] + [playbook, {pb_words} words]"),
            ("front", f"[top-{TOP_K} retrieved facts for this question] + [playbook, {pb_words} words]"),
            ("back", f"[playbook, {pb_words} words] + [top-{TOP_K} retrieved facts for this question]"),
            ("router", f"[playbook, {pb_words} words]   … MemoryRouter attaches the memory itself")]
    for a, layout in rows:
        emit("text", f"    {ARM_LABEL[a]:<34} system = {layout}")
    note("the playbook is the same bytes for every customer and every question; the memory part changes with each question")
    note("providers cache prompt prefixes: a request is billed at the cache price for the longest prefix it shares "
         "with a recent request (OpenAI: from 1,024 tokens up)")
    emit("layouts", "", rows=[dict(arm=a, layout=l) for a, l in rows], playbook_words=pb_words)


# --------------------------------------------------------------------------- step 4: the traffic

def ask_one(cli: CLI, world: dict, d: dict, qi: int, q: dict | None, text: str, p: dict, echo: bool = False,
            pool: int | None = None) -> list[dict]:
    """One question through A–D. The arm order rotates with qi, so no arm always warms the cache for another."""
    c = customer(d, q["customer"]) if q else who_in(d, text)
    facts = retrieve(cli, world, text, echo=echo)
    order = ARMS[qi % 4:] + ARMS[:qi % 4]
    rows = []
    for a in order:
        r = router(cli, system_for(d, a, c, facts), text, world["boundary"] if a == "router" else None,
                   echo=echo and a in ("front", "router"))
        if not r:
            return []
        ok = bool(re.search(q["expect"], r["reply"], re.I)) if q else None
        cached = r["cached"]
        rows.append(dict(q=qi, pool=pool, question=text, customer=c and c["key"], arm=a, ok=ok, reply=r["reply"], seen=r["seen"],
                         prompt_tokens=r["prompt_tokens"], cached=cached, completion_tokens=r["completion_tokens"],
                         usd=cost(p, r["prompt_tokens"], cached or 0, r["completion_tokens"]), facts=facts))
    # The same call as D without the boundary: if D sent no more tokens than this, MemoryRouter attached nothing (∅).
    # It runs after the four arms, so it never warms the cache for them; it is not part of the bill.
    base = router(cli, d["playbook"], text, None, max_tokens=1)
    for r in rows:
        if r["arm"] == "router":
            r["empty"] = bool(base) and r["prompt_tokens"] <= base["prompt_tokens"]
    rows.sort(key=lambda r: ARMS.index(r["arm"]))
    return rows


def fmt_seen(m) -> str:
    return "head new" if m is None else f"head sent {m:.0f} min ago" if m >= 1 else "head sent <1 min ago"


def fmt_cached(c) -> str:
    return "  n/a" if c is None else f"{c:>5,}"


def show_question(rows: list[dict], n: int, total: int) -> None:
    r0 = rows[0]
    emit("text", f"\n  Q{n}/{total}  “{r0['question']}”  (#{r0['pool']} of the pool)")
    for r in rows:
        mark = "∅" if r.get("empty") else ("✓" if r["ok"] else ("✗" if r["ok"] is False else " "))
        hit = "cache hit " if (r["cached"] or 0) > 0 else "cache miss"
        emit("text", f"    {SHORT[r['arm']]}  {r['prompt_tokens']:>5,} prompt · {fmt_cached(r['cached'])} cached  {hit}  "
                     f"{fmt_seen(r['seen']):<22} {mark}  {' '.join(r['reply'].split())[:56]}")
    emit("question", "", n=n, total=total, rows=rows)


def pick_traffic(d: dict, seed: int) -> list[int]:
    """Today's traffic: per customer, a few questions drawn from the 60 the facts can answer (one per fact), in rounds.

    Real support traffic rarely sends the same question with the same memory twice within minutes; re-running this
    demo with fixed questions would. DEMO_SEED=<n> replays a draw."""
    rng = random.Random(seed)
    n = d["traffic"]["per_customer"]
    recent = recent_questions()
    picks = {}
    for c in d["customers"]:
        mine = [i for i, q in enumerate(d["questions"]) if q["customer"] == c["key"]]
        fresh = [i for i in mine if d["questions"][i]["text"] not in recent]
        picks[c["key"]] = rng.sample(fresh, n) if len(fresh) >= n else fresh + rng.sample([i for i in mine if i not in fresh], n - len(fresh))
    return [picks[c["key"]][r] for r in range(n) for c in d["customers"]]


def run_traffic(cli: CLI, world: dict, d: dict, p: dict, picks: list[int]) -> list[dict]:
    rows: list[dict] = []
    qs = [d["questions"][i] for i in picks]
    for i, q in enumerate(qs):
        mine = ask_one(cli, world, d, i, q, q["text"], p, echo=i == 0, pool=picks[i] + 1)
        question_sent(q["text"])
        if not mine:
            return rows
        show_question(mine, i + 1, len(qs))
        rows += mine
    return rows


# --------------------------------------------------------------------------- step 5: the bill

def tally(rows: list[dict], arm: str) -> dict:
    mine = [r for r in rows if r["arm"] == arm]
    pt = sum(r["prompt_tokens"] for r in mine)
    cached = sum(r["cached"] or 0 for r in mine)
    return dict(arm=arm, calls=len(mine), prompt=pt, cached=cached, share=cached / pt if pt else 0.0,
                hits=sum(1 for r in mine if (r["cached"] or 0) > 0),
                new=sum(1 for r in mine if r["seen"] is None),
                new_hits=sum(1 for r in mine if r["seen"] is None and (r["cached"] or 0) > 0),
                seen_hits=sum(1 for r in mine if r["seen"] is not None and (r["cached"] or 0) > 0),
                unreported=sum(1 for r in mine if r["cached"] is None),
                right=sum(1 for r in mine if r["ok"] and not r.get("empty")),
                empty=sum(1 for r in mine if r.get("empty")),
                usd=sum(r["usd"] for r in mine), usd_nocache=0.0)


def show_bill(rows: list[dict], p: dict) -> dict:
    bill = {a: tally(rows, a) for a in ARMS}
    for a in ARMS:
        b = bill[a]
        b["usd_nocache"] = sum(cost(p, r["prompt_tokens"], 0, r["completion_tokens"]) for r in rows if r["arm"] == a)
    n = bill["front"]["calls"] or 1
    emit("text", f"    {'':34} {'prompt':>8} {'cached':>8} {'hits':>6} {'right':>6} {'USD, these calls':>17} {'USD per 1M calls':>17}")
    for a in ARMS:
        b = bill[a]
        right = f"{b['right']}/{b['calls'] - b['empty']}" + (f" ∅{b['empty']}" if b["empty"] else "")
        emit("text", f"    {ARM_LABEL[a]:<34} {b['prompt']:>8,} {b['cached']:>8,} {b['hits']:>3}/{b['calls']:<2} {right:>6} "
                     f"{b['usd']:>17.6f} {b['usd'] / max(b['calls'], 1) * 1e6:>17,.0f}")
    if any(b["unreported"] for b in bill.values()):
        note("calls where the provider did not report cached_tokens are counted as 0 cached (marked n/a above)")
    emit("text", "")
    for a in ARMS:
        b = bill[a]
        emit("text", f"    {ARM_LABEL[a]:<34} head of the prompt new {b['new']:>2}× → {b['new_hits']} hit(s) · "
                     f"head sent earlier {b['calls'] - b['new']:>2}× → {b['seen_hits']} hit(s)")
    note("a hit needs the start of the prompt (the first ~1,024 tokens) to have been sent in the last minutes; "
         "what each request starts with is logged locally in out/sent.json")
    odd = {SHORT[a]: bill[a]["new_hits"] for a in ARMS if bill[a]["new_hits"]}
    if odd:
        note("hits on a head this folder had not sent (" + ", ".join(f"{k} {v}" for k, v in odd.items()) + "): the "
             "provider's cache is not per folder — another clone or the browser companion sent that start recently")
    a = bill["stuffed"]
    if a["hits"]:
        note(f"A hit {a['hits']} time(s): a customer's file is the same bytes every time, so their second question within "
             "minutes shares it; with many customers and returns spread over hours, it rarely does")
    f, k = bill["front"], bill["back"]
    if f["calls"] and k["calls"]:
        diff = (f["usd"] - k["usd"]) / n * 1e6
        note(f"B and C sent {f['prompt']:,} vs {k['prompt']:,} prompt tokens and were right {f['right']} vs {k['right']} times; "
             f"B paid {diff:,.0f} USD more per million calls, all of it the cache price it never got")
    emit("bill", "", bill=bill, prices=p)
    return bill


# --------------------------------------------------------------------------- step 6: B next to C

def show_pairs(rows: list[dict]) -> dict:
    by = {}
    for r in rows:
        by.setdefault(r["q"], {})[r["arm"]] = r
    same_tokens = same_reply = b_hits = c_hits = 0
    pairs = []
    for qi in sorted(by):
        b, c = by[qi].get("front"), by[qi].get("back")
        if not b or not c:
            continue
        st, sr = b["prompt_tokens"] == c["prompt_tokens"], " ".join(b["reply"].split()) == " ".join(c["reply"].split())
        same_tokens += st
        same_reply += sr
        b_hits += (b["cached"] or 0) > 0
        c_hits += (c["cached"] or 0) > 0
        pairs.append(dict(q=qi + 1, tokens=(b["prompt_tokens"], c["prompt_tokens"]), cached=(b["cached"], c["cached"]),
                          same_reply=sr))
        emit("text", f"    Q{qi + 1:<3} B {b['prompt_tokens']:>5,} / {fmt_cached(b['cached'])} cached    "
                     f"C {c['prompt_tokens']:>5,} / {fmt_cached(c['cached'])} cached    "
                     f"{'same tokens' if st else 'tokens differ'} · {'same reply' if sr else 'reply differs'}")
    n = len(pairs)
    note(f"{same_tokens}/{n} questions: B and C sent exactly the same number of tokens; {same_reply}/{n}: the same reply word for word")
    note(f"cache hits: B {b_hits}/{n}, C {c_hits}/{n} — the only thing that moved is where the memory block sits")
    b_rep = sum(1 for r in rows if r["arm"] == "front" and r["seen"] is not None)
    if b_rep:
        note(f"{b_rep} of B's requests started with a memory block sent minutes earlier (the same question drew the same "
             "facts, e.g. in an earlier run of this demo); B can only hit on such exact repeats")
    out = dict(pairs=pairs, same_tokens=same_tokens, same_reply=same_reply, b_hits=b_hits, c_hits=c_hits, n=n)
    emit("pairs", "", **out)
    return out


TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, f"Set up — {len(d['customers'])} customers' memory, one boundary, the price list")
    world = setup(cli)
    p = prices(cli)
    show_prices(p)

    banner(3, TOTAL, "Four ways to build Dock's prompt")
    show_layouts(d)

    banner(4, TOTAL, f"Traffic — {d['traffic']['per_customer'] * len(d['customers'])} questions from {len(d['customers'])} "
                     f"customers, each through A–D")
    note(f"temperature 0, {MODEL}; one `search` per question feeds both B and C, so their memory block is identical; "
         "the arm order rotates every question")
    seed = int(os.environ.get("DEMO_SEED") or time.time())
    recent = recent_questions()
    picks = pick_traffic(d, seed)
    repeats = sum(1 for i in picks if d["questions"][i]["text"] in recent)
    note(f"traffic seed {seed} (DEMO_SEED={seed} replays this draw); {len(recent)} question(s) asked from this folder in "
         f"the last hour are left out of the draw" + (f" — the pool ran short, {repeats} repeat(s)" if repeats else ""))
    rows = run_traffic(cli, world, d, p, picks)

    banner(5, TOTAL, "The bill — same memory, four prices")
    bill = show_bill(rows, p) if rows else {}

    banner(6, TOTAL, "B next to C — the same request, cut in a different place")
    pairs = show_pairs(rows) if rows else {}

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(world=world, prices=p, seed=seed, picks=[i + 1 for i in picks], answers=rows, bill=bill, pairs=pairs, tokens=tok,
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
    """A question number (1–60) or any question, through A–D, twice in a row: the second time is the warm cache."""
    d = load_data()
    world = load_world(cli)
    p = prices(cli, echo=False)
    qs = d["questions"]
    q = qs[int(what) - 1] if what.strip().isdigit() and 1 <= int(what) <= len(qs) else None
    text = q["text"] if q else what.strip()
    c = customer(d, q["customer"]) if q else who_in(d, text)
    emit("text", f"  “{text}”" + (f"  (customer: {c['name']})" if c else "  (no customer named: A gets the playbook alone)"))
    rows = []
    for rnd in (1, 2):
        mine = ask_one(cli, world, d, 0, q, text, p, echo=rnd == 1, pool=int(what) if q else None)
        if not mine:
            die("MemoryRouter is not available on this account (no balance)")
        emit("text", f"\n  round {rnd}{' (the same request again, a moment later)' if rnd == 2 else ''}")
        for r in mine:
            mark = "∅" if r.get("empty") else ("✓" if r["ok"] else ("✗" if r["ok"] is False else " "))
            emit("text", f"    {ARM_LABEL[r['arm']]:<34} {r['prompt_tokens']:>5,} prompt · {fmt_cached(r['cached'])} cached  "
                         f"{r['usd'] * 1e6:>6,.0f} USD per 1M  {fmt_seen(r['seen']):<22} {mark}")
            if rnd == 1:
                emit("text", wrap(r["reply"], "      | ", 100))
        rows += [dict(r, round=rnd) for r in mine]
    b1, b2 = (next((r for r in rows if r["arm"] == "front" and r["round"] == k), None) for k in (1, 2))
    if b1 and b2 and b1["facts"] == b2["facts"]:
        note("search returned the same facts both times, so B's round-2 prompt was byte-identical to round 1 — that is "
             "the only way B shares a prefix; in real traffic the next question retrieves a different block")
    elif b1 and b2:
        note("search returned a different set of facts the second time, so even the same question gave B a new "
             "prompt head; C and D still started with the playbook")
    emit("asked", "", question=text, rows=rows)
    return rows


def show_facts(cli: CLI) -> None:
    """What the project remembers, per customer (`fact list`)."""
    d = load_data()
    world = load_world(cli)
    facts = project_facts(cli, world["project"], echo=True)
    for c in d["customers"]:
        mine = [f["fact"] for f in facts if f["fact"].startswith(c["name"])]
        emit("text", f"\n  {c['name']} ({c['company']}) — {len(mine)} facts")
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
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


EXAMPLE = "37"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = what the project remembers per customer; "
                         "ask <1-60 | \"question\"> = one question through A–D, twice; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question number (1-60) or a question that names a customer")
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
            die(f'usage: python3 demo.py ask <1-60 | "question">   (e.g. python3 demo.py ask {EXAMPLE})')
        banner(2, 2, "One question through A–D, twice")
        ask(cli, args.args[0])
        return
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

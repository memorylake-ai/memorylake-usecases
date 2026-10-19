#!/usr/bin/env python3
"""
Frozen-chain memory: the memory was updated, but the Responses API's stored chain
still holds the old value — a MemoryLake demo driven entirely by the `memorylake`
CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Brindle Lane Deliveries (fictional) delivers parcels across town.  Its dispatch
assistant, Dispatchbot, reads delivery notes through MemoryRouter: a boundary over
one MemoryLake project that holds each recipient's drop-off gate code.

Dispatchbot uses the Responses API the way its docs suggest: `store: true`, then
only the new message plus `previous_response_id` — the provider keeps the history.

The dispatcher has the same two-turn chat for six parcels:
  1. "I'm loading Hollis Grant's parcel BL-2201. What's the drop-off gate code?"
     — MemoryRouter attaches the gate code; the answer is right.
  2. Between the turns, every recipient phones in a new gate code.  The app records
     it with `fact update` — `fact list` shows the new code, `fact trace` the old one
     in the fact's history.
  3. The second turn asks for the code again, two ways ("confirm the code" / "which
     code should the driver punch in").

What it shows
-------------
  · The memory is current: a fresh call through the boundary answers the new code.
  · Continuing the chain without the boundary — the way to avoid MemoryRouter
    attaching someone else's notes on a follow-up — answers the OLD code every time:
    the notes attached on turn 1 were stored in the chain with it.
  · Continuing the chain with the boundary puts the old and the new note side by side.
    Which one wins depends on how the dispatcher asks.
  · Two ways that answer the new code: send the conversation yourself (MemoryRouter
    attaches fresh notes on every turn and nothing is frozen), or keep the chain and
    tell the model that the notes on the newest message are current.

Model output varies run to run; every reply is graded and printed, and the README
reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (55 calls on
gpt-4o-mini per run).  Turn-1 responses are stored on the provider's side
(`store: true`) and cannot be deleted through MemoryLake.
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

PREFIX = "mlu-fzc"  # memorylake-usecases / frozen-chain-memory

SYSTEM = ("You are Dispatchbot, the dispatch assistant at Brindle Lane Deliveries. The dispatcher tells you which "
          "parcel they are handling and asks about it. Answer in one or two short sentences using the delivery notes "
          "you are given. If a note is not on file, say so.")
NEWEST = (" Delivery notes can change during a conversation: if two notes disagree, the notes attached to the "
          "newest message are current.")

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




MODEL = dict(protocol="responses", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Responses API)")

ARMS = {
    "history": dict(label="whole conversation in the request", short="history",
                    how="turn 2 sends turn 1 + Dispatchbot's reply (with the old code) + the new message in `input`, "
                        "with the boundary"),
    "bare": dict(label="previous_response_id, no boundary", short="chain only",
                 how="turn 2 sends only the new message + `previous_response_id`, without `--boundary`"),
    "chain": dict(label="previous_response_id + boundary", short="chain+memory",
                  how="turn 2 sends only the new message + `previous_response_id`, with the boundary"),
    "newest": dict(label="previous_response_id + boundary + “newest notes are current”", short="+newest",
                   how="the same as chain + boundary, and turn 2's `instructions` add: if two notes disagree, the "
                       "notes attached to the newest message are current"),
}
ASKS = ("confirm", "driver")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "recipients.json").read_text(encoding="utf-8"))


def fact_text(d: dict, r: dict, which: str) -> str:
    return d["fact_template"].format(name=r["name"], parcel=r["parcel"], code=r[which])


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def grade(r: dict, d: dict, reply: str) -> dict:
    """new = the new code and not the old one; old = the old code and not the new one; both / none otherwise."""
    codes = set(re.findall(r"(?<!\d)\d{4}(?!\d)", reply))
    new, old = r["new"] in codes, r["old"] in codes
    verdict = "new" if new and not old else "old" if old and not new else "both" if new else "none"
    return dict(verdict=verdict, ok=verdict == "new")


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("notes"), "--by-custom-id", scoped=True, echo=echo)


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
    if old is not None or demo_boundaries(cli):
        note("an earlier run left the delivery notes in place; starting over with a fresh project")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Brindle Lane: delivery notes", "--custom-id", cid("notes"),
                   "--description", "Drop-off gate codes a dispatch assistant reads. Demo data.", scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: Dispatchbot passes this boundary to MemoryRouter on every call.
    b = cli.run("boundary", "create", "--name", "Dispatchbot", "--custom-id", cid("boundary"),
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


# --------------------------------------------------------------------------- step 3: what Dispatchbot remembers

def store(cli: CLI, world: dict, d: dict) -> dict[str, str]:
    """Add one gate-code fact per recipient; return recipient name → fact id."""
    want = [fact_text(d, r, "old") for r in d["recipients"]]
    try:
        cli.run("fact", "add", "--project", world["project"], *want, scoped=True)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        # `fact add` is not idempotent, so a dropped call is not retried blindly: add only what is still missing.
        have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
        missing = [t for t in want if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", "--project", world["project"], *missing, scoped=True, echo=False)
    facts = list_facts(cli, world["project"])
    if sorted(f["fact"] for f in facts) != sorted(want):
        die(f"the project holds {len(facts)} facts, expected {len(want)}")
    ids = {}
    for f in facts:
        r = next(r for r in d["recipients"] if f["fact"].startswith(r["name"] + " "))
        ids[r["name"]] = f["id"]
    note(f"stored {len(facts)} facts: one drop-off gate code per recipient")
    emit("text", "\n" + "\n".join(f"   - {t}" for t in want))
    emit("memory", "", facts=want, phase="before")
    time.sleep(10)  # a new fact is in `fact list` at once; retrieval takes a few seconds to see it
    return ids


# --------------------------------------------------------------------------- MemoryRouter calls

_TOK = threading.Lock()
_BODY = threading.Lock()


def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, max_tokens: int = 150,
           echo: bool = True) -> dict | None:
    """One MemoryRouter call over the Responses API. `body` holds instructions, earlier input, store and
    previous_response_id."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = [MODEL["protocol"], prompt, "--model", MODEL["model"], "--max-tokens", str(max_tokens),
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
            note("model replies are skipped. Top up in the console to see them.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    text = "".join(c.get("text", "") for o in res.get("output") or [] if o.get("type") == "message"
                   for c in o.get("content") or [] if c.get("type") == "output_text")
    u = res.get("usage") or {}
    with _TOK:
        cli.tokens["prompt"] += u.get("input_tokens", 0)  # type: ignore[attr-defined]
        cli.tokens["completion"] += u.get("output_tokens", 0)  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(id=res.get("id"), reply=text.strip(), input_tokens=u.get("input_tokens", 0),
                seconds=round(time.time() - t0, 1))


def write_body(name: str, instructions: str, input_: list[dict], store: bool = False,
               previous: str | None = None) -> Path:
    """Each call has its own body file, written once and moved into place whole (calls run in parallel)."""
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    body = {"model": MODEL["model"], "instructions": instructions, "input": input_, "store": store}
    if previous:
        body["previous_response_id"] = previous
    with _BODY:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return path


def turn1(cli: CLI, world: dict, i: int, r: dict, d: dict) -> dict:
    say = d["turn1"].format(**r)
    body = write_body(f"turn1-{i}", SYSTEM, [], store=True)
    res = router(cli, say, body, boundary=world["boundary"], echo=i == 0)
    reply = res["reply"] if res else ""
    codes = set(re.findall(r"(?<!\d)\d{4}(?!\d)", reply))
    return dict(recipient=r["name"], say=say, id=res and res["id"], reply=reply, said_old=r["old"] in codes,
                input_tokens=res and res["input_tokens"])


def turn2(cli: CLI, world: dict, arm: str, ask: str, i: int, r: dict, d: dict, first: dict,
          echo: bool = False) -> dict | None:
    say = d["turn2"][ask].format(**r)
    name = f"{arm}-{ask}-{i}"
    if arm == "history":
        body = write_body(name, SYSTEM, [{"role": "user", "content": first["say"]},
                                         {"role": "assistant", "content": first["reply"] or "(no reply)"}])
    else:
        body = write_body(name, SYSTEM + (NEWEST if arm == "newest" else ""), [], previous=first["id"])
    return router(cli, say, body, boundary=None if arm == "bare" else world["boundary"], echo=echo)


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


def show_row(row: dict, r: dict) -> str:
    g = row["grade"]
    tail = ""
    if g and g["verdict"] == "old":
        tail = f"   ← the old code ({r['old']})"
    elif g and g["verdict"] == "both":
        tail = "   ← both codes"
    elif g and g["verdict"] == "none":
        tail = "   ← no gate code"
    return f"    {mark(g and g['ok'])} {row['recipient']:<15} {textwrap.shorten(row['reply'], 80)}{tail}"


def second_turns(cli: CLI, world: dict, arm: str, firsts: list[dict], d: dict) -> list[dict]:
    recs = d["recipients"]
    jobs = [(ask, i) for ask in ASKS for i in range(len(recs))]
    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(lambda j: turn2(cli, world, arm, j[0], j[1], recs[j[1]], d, firsts[j[1]],
                                            echo=j == jobs[0]), jobs))
    rows = []
    for (ask, i), x in zip(jobs, res):
        r = recs[i]
        row = dict(arm=arm, ask=ask, recipient=r["name"], reply=x["reply"] if x else "",
                   input_tokens=x and x["input_tokens"], grade=grade(r, d, x["reply"]) if x else None)
        rows.append(row)
    for ask in ASKS:
        emit("text", f"\n    Dispatcher: “{d['turn2'][ask].format(name='<recipient>', parcel='<parcel>')}”")
        part = [row for row in rows if row["ask"] == ask]
        for row in part:
            r = next(r for r in recs if r["name"] == row["recipient"])
            emit("text", show_row(row, r))
            emit("turn", "", **row)
        got = [x for x in part if x["grade"]]
        if got:
            new = sum(x["grade"]["ok"] for x in got)
            old = sum(x["grade"]["verdict"] == "old" for x in got)
            toks = [x["input_tokens"] for x in got]
            emit("text", f"    {new}/{len(got)} new code" + (f" · {old} old code" if old else "")
                 + f" · input_tokens {min(toks)}–{max(toks)}")
    return rows


# --------------------------------------------------------------------------- step 5: the recipients call

def update_codes(cli: CLI, world: dict, d: dict, ids: dict[str, str]) -> list[str]:
    recs = d["recipients"]
    for r in recs:
        emit("text", "    " + d["call_note"].format(**r) + f"  {r['old']} → {r['new']}")

    def up(r):
        echo = r is recs[0]
        cli.run("fact", "update", ids[r["name"]], "--project", world["project"], "--text", fact_text(d, r, "new"),
                scoped=True, echo=echo)
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(up, recs))
    note(f"updated {len(recs)} facts in place; the old text is kept in each fact's history")
    t = cli.run("fact", "trace", ids[recs[0]["name"]], "--project", world["project"], scoped=True) or {}
    trail = []
    for e in t.get("trace") or []:
        line = f"    {e.get('event'):<7} {e.get('source_kind', ''):<7} " + (
            f"{e.get('old_fact')}  →  {e.get('new_fact')}" if e.get("old_fact") else f"{e.get('new_fact')}")
        trail.append(line)
    emit("text", "\n".join(trail))
    emit("trace", "", recipient=recs[0]["name"], trace=t.get("trace") or [])
    facts = sorted(f["fact"] for f in list_facts(cli, world["project"]))
    want = sorted(fact_text(d, r, "new") for r in recs)
    if facts != want:
        die("`fact list` does not show the new gate codes yet; run the demo again")
    emit("text", "\n".join(f"   - {t}" for t in facts))
    emit("memory", "", facts=facts, phase="after")
    time.sleep(15)  # give retrieval a few seconds to see the edited text
    return facts


def fresh_check(cli: CLI, world: dict, d: dict) -> dict | None:
    """A new conversation through the same boundary: proves MemoryRouter now attaches the new code."""
    r = d["recipients"][0]
    say = d["turn2"]["confirm"].format(**r)
    res = router(cli, say, write_body("fresh-0", SYSTEM, []), boundary=world["boundary"])
    if not res:
        return None
    g = grade(r, d, res["reply"])
    emit("text", show_row(dict(recipient=r["name"], reply=res["reply"], grade=g), r))
    out = dict(recipient=r["name"], reply=res["reply"], grade=g, input_tokens=res["input_tokens"])
    emit("fresh", "", **out)
    if not g["ok"]:
        note("the fresh call did not answer the new code yet — retrieval may still be catching up; the numbers below "
             "may be off for this run")
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    recs = d["recipients"]
    if OUT.exists() and (OUT / "report.json").exists():  # keep the previous run's numbers
        runs_dir = OUT / "runs"
        runs_dir.mkdir(exist_ok=True)
        os.replace(OUT / "report.json", runs_dir / f"report-{int((OUT / 'report.json').stat().st_mtime * 1000)}.json")
    banner(2, TOTAL, "Set up — one memory project and one boundary Dispatchbot passes to MemoryRouter")
    world = setup(cli)

    banner(3, TOTAL, "What Dispatchbot remembers — one gate code per recipient")
    ids = store(cli, world, d)

    banner(4, TOTAL, "Turn 1 — the dispatcher names the parcel (stored on the provider's side: store: true)")
    note("MemoryRouter attaches the gate code; the response, with that note in it, is stored in the chain")
    with ThreadPoolExecutor(6) as pool:
        firsts = list(pool.map(lambda i: turn1(cli, world, i, recs[i], d), range(len(recs))))
    for f in firsts:
        emit("text", f"    Dispatcher: {f['say']}\n      Dispatchbot: {textwrap.shorten(f['reply'], 90)}")
        emit("first", "", **f)
    if not all(f["id"] for f in firsts):
        die("turn 1 did not come back with a response id")
    note(f"{sum(f['said_old'] for f in firsts)}/{len(firsts)} answered the gate code on file")

    banner(5, TOTAL, "The recipients call — every gate code changes (fact update)")
    after = update_codes(cli, world, d, ids)
    note("a new conversation through the same boundary, to check MemoryRouter now attaches the new code:")
    fresh = fresh_check(cli, world, d)

    runs: dict[str, list[dict]] = {}
    for step, arm, title in ((6, "history", "Turn 2, sending the conversation yourself"),
                             (7, "bare", "Turn 2 on the chain, without the boundary"),
                             (8, "chain", "Turn 2 on the chain, with the boundary")):
        banner(step, TOTAL, f"{title} — {ARMS[arm]['label']}")
        note(ARMS[arm]["how"])
        runs[arm] = second_turns(cli, world, arm, firsts, d)
        if arm == "bare":
            note("`fact list` above shows the new codes; the chain still carries the note attached on turn 1")
        if arm == "chain":
            emit("text", f"\n  The fix that keeps the chain — {ARMS['newest']['label']}")
            note(ARMS["newest"]["how"])
            runs["newest"] = second_turns(cli, world, "newest", firsts, d)

    banner(9, TOTAL, "Scoreboard — the same six chats and model; only how turn 2 is sent differs")
    cols = [(a, k) for a in ARMS for k in ASKS]
    lines = [f"\n    {'':<24}" + "".join(f"{ARMS[a]['short']:>16}" for a in ARMS),
             f"    {'parcel':<24}" + "".join(f"{'confirm driver':>16}" for _ in ARMS)]
    rows = []
    for i, r in enumerate(recs):
        cells = {}
        for a, k in cols:
            x = next(x for x in runs[a] if x["recipient"] == r["name"] and x["ask"] == k)
            cells[f"{a}:{k}"] = x["grade"]["ok"] if x["grade"] else None
        rows.append(dict(recipient=r["name"], parcel=r["parcel"], **cells))
        lines.append(f"    {r['name'] + ' ' + r['parcel']:<24}" + "".join(
            f"{mark(cells[f'{a}:confirm']) + '      ' + mark(cells[f'{a}:driver']):>13}   " for a in ARMS))
    tot = {f"{a}:{k}": sum(1 for x in runs[a] if x["ask"] == k and x["grade"] and x["grade"]["ok"]) for a, k in cols}
    old = {a: sum(1 for x in runs[a] if x["grade"] and x["grade"]["verdict"] == "old") for a in ARMS}
    n = len(recs)
    lines.append(f"    {'new code':<24}" + "".join(
        f"{str(tot[a + ':confirm']) + '/' + str(n) + '    ' + str(tot[a + ':driver']) + '/' + str(n):>15} " for a in ARMS))
    lines.append(f"    {'old code (both asks)':<24}" + "".join(f"{str(old[a]) + '/' + str(2 * n):>16}" for a in ARMS))
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=rows, totals=tot, old=old)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(firsts=firsts, fresh=fresh, memory_after=after, runs=runs, totals=tot, old=old, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok,  # type: ignore[attr-defined]
                   run_id=f"{PREFIX}-{int(t0 * 1000)}")
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} input + {tok['completion']:,} "
                 f"output tokens, billed to your MemoryLake balance.\n"
                 f"  Turn-1 responses were stored with the provider (store: true); MemoryLake cannot delete them.\n"
                 f"  Every reply and request body: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot, old=old)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_facts(cli: CLI) -> None:
    world = load_world(cli)
    facts = list_facts(cli, world["project"])
    emit("text", "\n".join(f"   - {f['fact']}" for f in sorted(facts, key=lambda f: f["fact"])))
    note(f"{len(facts)} facts behind boundary {world['boundary']}")


def ask_one(cli: CLI, recipient: str, ask: str = "confirm") -> dict:
    """One recipient's two-turn chat with a gate-code change in between, sent the four ways (the web page's Try view).
    The fact is put back to the old code first and left on the new one."""
    d = load_data()
    i = next((n for n, r in enumerate(d["recipients"]) if r["name"] == recipient), None)
    if i is None:
        die(f"unknown recipient {recipient!r}")
    world = load_world(cli)
    r = d["recipients"][i]
    fid = next(f["id"] for f in list_facts(cli, world["project"], echo=False) if f["fact"].startswith(r["name"] + " "))
    cli.run("fact", "update", fid, "--project", world["project"], "--text", fact_text(d, r, "old"), scoped=True)
    time.sleep(15)
    first = turn1(cli, world, i, r, d)
    cli.run("fact", "update", fid, "--project", world["project"], "--text", fact_text(d, r, "new"), scoped=True)
    time.sleep(15)
    out = dict(recipient=recipient, ask=ask, first=first)
    for arm in ARMS:
        x = turn2(cli, world, arm, ask, i, r, d, first, echo=arm == "chain")
        out[arm] = dict(reply=x["reply"] if x else "", grade=grade(r, d, x["reply"]) if x else None,
                        input_tokens=x and x["input_tokens"])
    return out


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
    if OUT.exists():
        for p in OUT.glob("body-*.json"):
            p.unlink()
        if (OUT / "report.json").exists():
            runs_dir = OUT / "runs"
            runs_dir.mkdir(exist_ok=True)
            os.replace(OUT / "report.json", runs_dir / f"report-{int((OUT / 'report.json').stat().st_mtime * 1000)}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = list the stored delivery notes; "
                         "ask = one recipient's chat with a code change in between, sent four ways; "
                         "cleanup = delete everything")
    ap.add_argument("--recipient", default="Hollis Grant", help="for ask: whose parcel the dispatcher names")
    ap.add_argument("--ask", default="confirm", choices=list(ASKS), help="for ask: how turn 2 asks for the code")
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
        banner(2, 2, "What Dispatchbot remembers")
        show_facts(cli)
        return
    if args.command == "ask":
        banner(2, 2, f"{args.recipient}'s parcel — the gate code changes between the turns; turn 2 sent four ways")
        r = ask_one(cli, args.recipient, args.ask)
        rec = next(x for x in load_data()["recipients"] if x["name"] == args.recipient)
        for arm in ARMS:
            emit("text", f"  {ARMS[arm]['label']}\n" + show_row(dict(recipient=args.recipient, **r[arm]), rec))
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py ask --recipient "Ruth Abara"` or `python3 demo.py facts`; '
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

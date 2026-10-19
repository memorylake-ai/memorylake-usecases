#!/usr/bin/env python3
"""
Response-chain memory: after an SDK upgrade to the Responses API's
`previous_response_id`, MemoryRouter only sees the new message — a MemoryLake demo
driven entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkfield Home (fictional) ships furniture.  Its packing assistant, Packbot, reads
customer notes through MemoryRouter: a boundary over one MemoryLake project that
holds, for six customers, the drop-off gate code and what must not go in the box.

A packer has the same two-turn chat for every order:
  1. "I'm packing Tomas Reyes's order LK-5400 now."
  2. "What's the gate code for the drop-off, and is there anything I must not put in the box?"

Packbot v1 (Chat Completions) sent the whole conversation on every turn.  Packbot v2
moved to the Responses API the way its docs suggest: `store: true`, then only the new
message plus `previous_response_id` — the provider keeps the history.

What it shows
-------------
  · v1, whole conversation in the request: every second turn gets the right customer's code.
  · v2, `previous_response_id` + boundary: MemoryRouter can only search with what is in the
    request body — "gate code / must not pack" — and attaches *another* customer's notes.
    Some answers come back with someone else's gate code.
  · A checker (the same call, told to copy out the memory attached to the latest message)
    shows whose notes MemoryRouter attached.
  · The control: v2 without the boundary on turn 2 is right — turn 1's memory is already in
    the provider's stored chain.  The wrong code comes from the second attachment.
  · The fix that keeps `previous_response_id`: put the subject into the new message.

Model output varies run to run; every reply is graded and printed, and the README reports
totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (30 calls on
gpt-4o-mini and 7 on claude-haiku-4-5 per run).  Turn-1 responses are stored on the provider's side (`store: true`)
and cannot be deleted through MemoryLake.
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

PREFIX = "mlu-rcm"  # memorylake-usecases / response-chain-memory

SYSTEM = ("You are Packbot, the packing assistant at Larkfield Home, a furniture shop. Packers tell you which order "
          "they are packing and ask about it. Answer in one or two short sentences using what you remember about our "
          "customers. If you do not know, say so.")


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
CHECKER = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")

ARMS = {
    "history": dict(label="v1 · whole conversation in the request", short="v1 history",
                    how="turn 2 sends turn 1 + Packbot's reply + the new message in `input`, with the boundary"),
    "chain": dict(label="v2 · previous_response_id", short="v2 chain",
                  how="turn 2 sends only the new message + `previous_response_id`, with the boundary"),
    "chain_bare": dict(label="v2 · previous_response_id, no boundary on turn 2", short="no boundary",
                       how="the same as v2, but turn 2 goes out without `--boundary`"),
    "chain_named": dict(label="v2 · previous_response_id, subject in the new message", short="named",
                        how="the same as v2, but the new message says whose order it is"),
}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "customers.json").read_text(encoding="utf-8"))


def fact_texts(d: dict) -> list[str]:
    return [t.format(**c) for c in d["customers"] for t in d["fact_templates"]]


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def first_name(name: str) -> str:
    return name.split()[0]


def grade(c: dict, d: dict, reply: str) -> dict:
    """Right = this customer's gate code and nobody else's. Wrong = another customer's code is in the reply."""
    codes = set(re.findall(r"(?<!\d)\d{4}(?!\d)", reply))
    others = [o for o in d["customers"] if o["name"] != c["name"] and o["gate"] in codes]
    ok = c["gate"] in codes and not others
    return dict(ok=ok, wrong=[o["name"] for o in others], said_code=c["gate"] in codes)


def whose(line: str, d: dict) -> str:
    for c in d["customers"]:
        if c["name"] in line or c["order"] in line:
            return c["name"]
    return "?"


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("customers"), "--by-custom-id", scoped=True, echo=echo)


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
        note("an earlier run left the customer notes in place; starting over with a fresh project")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Larkfield Home: customer delivery notes", "--custom-id", cid("customers"),
                   "--description", "Gate codes and packing restrictions a packing assistant reads. Demo data.", scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: Packbot passes this boundary to MemoryRouter on every call.
    b = cli.run("boundary", "create", "--name", "Packbot", "--custom-id", cid("boundary"),
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


# --------------------------------------------------------------------------- step 3: what Packbot remembers

def store(cli: CLI, world: dict) -> list[str]:
    d = load_data()
    want = fact_texts(d)
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
    facts = [f["fact"] for f in list_facts(cli, world["project"])]
    if sorted(facts) != sorted(want):
        die(f"the project holds {len(facts)} facts, expected {len(want)}")
    note(f"stored {len(facts)} facts: {len(d['customers'])} customers × gate code + what must not go in the box")
    emit("text", "\n" + "\n".join(f"   - {t}" for t in want))
    emit("memory", "", facts=want, customers=[c["name"] for c in d["customers"]])
    time.sleep(10)  # a new fact is in `fact list` at once; retrieval takes a few seconds to see it
    return facts


# --------------------------------------------------------------------------- MemoryRouter calls

_TOK = threading.Lock()


def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, max_tokens: int = 200,
           echo: bool = True, tool: dict = MODEL) -> dict | None:
    """One MemoryRouter call. For `responses`, `body` holds instructions, earlier input, store and
    previous_response_id; for the checker (`messages`), the system prompt and the earlier messages."""
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
            note("model replies are skipped. Top up in the console to see them.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    if tool["protocol"] == "messages":
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    else:
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
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    body = {"model": MODEL["model"], "instructions": instructions, "input": input_, "store": store}
    if previous:
        body["previous_response_id"] = previous
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def turn1(cli: CLI, world: dict, i: int, c: dict, d: dict) -> dict:
    say = d["turn1"].format(**c)
    body = write_body(f"turn1-{i}", SYSTEM, [], store=True)
    r = router(cli, say, body, boundary=world["boundary"], echo=i == 0)
    return dict(customer=c["name"], say=say, id=r and r["id"], reply=r["reply"] if r else "")


def turn2(cli: CLI, world: dict, arm: str, i: int, c: dict, d: dict, first: dict, echo: bool = False) -> dict | None:
    say = d["turn2_named"].format(**c) if arm == "chain_named" else d["turn2"]
    name = f"{arm}-{i}"
    if arm == "history":
        body = write_body(name, SYSTEM, [{"role": "user", "content": first["say"]},
                                               {"role": "assistant", "content": first["reply"] or "(no reply)"}])
    else:
        body = write_body(name, SYSTEM, [], previous=first["id"])
    return router(cli, say, body, boundary=None if arm == "chain_bare" else world["boundary"],
                  echo=echo)


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


def show_row(row: dict) -> str:
    g = row["grade"]
    tail = ""
    if g and g["wrong"]:
        tail = "   ← " + ", ".join(f"{w}'s code" for w in g["wrong"])
    elif g and not g["said_code"]:
        tail = "   ← no gate code"
    return (f"    {mark(g and g['ok'])} {row['customer']:<14} {textwrap.shorten(row['reply'], 84)}{tail}")


def second_turns(cli: CLI, world: dict, arm: str, firsts: list[dict], d: dict) -> list[dict]:
    custs = d["customers"]
    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(lambda i: turn2(cli, world, arm, i, custs[i], d, firsts[i], echo=i == 0), range(len(custs))))
    rows = []
    for i, (c, r) in enumerate(zip(custs, res)):
        row = dict(arm=arm, customer=c["name"], reply=r["reply"] if r else "", input_tokens=r and r["input_tokens"],
                   grade=grade(c, d, r["reply"]) if r else None)
        rows.append(row)
        emit("text", show_row(row))
        emit("turn", "", **row)
    got = [r for r in rows if r["grade"]]
    if got:
        ok = sum(r["grade"]["ok"] for r in got)
        wrong = [r for r in got if r["grade"]["wrong"]]
        toks = [r["input_tokens"] for r in got]
        emit("text", f"\n    {ok}/{len(got)} right"
             + (f" · {len(wrong)} with another customer's gate code" if wrong else "")
             + f" · input_tokens {min(toks)}–{max(toks)}")
    return rows


# --------------------------------------------------------------------------- step 7: what MemoryRouter attached

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). Then answer the question in one sentence.")


def attached_lines(reply: str) -> list[str]:
    block = reply.split("```")[1] if reply.count("```") >= 2 else reply
    return [ln.strip()[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]


def checker_call(cli: CLI, world: dict, name: str, history: list[dict], say: str, echo: bool = False) -> dict:
    """Send Claude the words MemoryRouter saw on turn 2, through the same boundary, told (in the system prompt) to copy
    out the memory first. v2's request body holds only the new message — the history lives with the provider, where
    MemoryRouter cannot read it — so the checker gets only the new message too."""
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    path.write_text(json.dumps({"model": CHECKER["model"], "system": CHECK_SYSTEM, "messages": history},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    r = router(cli, say, path, boundary=world["boundary"], max_tokens=1500, echo=echo, tool=CHECKER)
    lines = attached_lines(r["reply"]) if r else []
    return dict(lines=lines)


def check(cli: CLI, world: dict, firsts: list[dict], d: dict) -> list[dict]:
    custs = d["customers"]
    jobs = [("chain", -1)] + [("history", i) for i in range(len(custs))]

    def go(job):
        arm, i = job
        if arm == "chain":
            return checker_call(cli, world, "check-chain", [], d["turn2"], echo=True)
        f = firsts[i]
        return checker_call(cli, world, f"check-history-{i}", [{"role": "user", "content": f["say"]},
                                                               {"role": "assistant", "content": f["reply"] or "(no reply)"}],
                            d["turn2"])

    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(go, jobs))
    chain = res[0]
    chain["whose"] = [whose(ln, d) for ln in chain["lines"]]
    out = []
    for i, c in enumerate(custs):
        h = res[i + 1]
        h["whose"] = [whose(ln, d) for ln in h["lines"]]
        out.append(dict(customer=c["name"], history=h, chain=chain))
    return out


def show_check(row: dict) -> str:
    def cell(x: dict) -> str:
        if not x["lines"]:
            return "(nothing copied)"
        names = []
        for w in x["whose"]:
            if w not in names:
                names.append(w)
        return ", ".join(first_name(n) if n != "?" else "?" for n in names) + f" ({len(x['lines'])} lines)"
    h, ch = row["history"], row["chain"]
    other = [w for w in ch["whose"] if w not in (row["customer"], "?")]
    return (f"    {row['customer']:<14} v1: {cell(h):<24} v2: {cell(ch)}"
            + ("   ← another customer's notes" if other else ""))


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — one memory project and one boundary Packbot passes to MemoryRouter")
    world = setup(cli)

    banner(3, TOTAL, "What Packbot remembers — two notes per customer")
    stored = store(cli, world)

    banner(4, TOTAL, "Turn 1 — the packer names the order (stored on the provider's side: store: true)")
    note("the same first turn feeds every version below, so only the second turn differs")
    custs = d["customers"]
    with ThreadPoolExecutor(6) as pool:
        firsts = list(pool.map(lambda i: turn1(cli, world, i, custs[i], d), range(len(custs))))
    for f in firsts:
        emit("text", f"    Packer: {f['say']}\n      Packbot: {textwrap.shorten(f['reply'], 90)}")
        emit("first", "", **f)
    if not all(f["id"] for f in firsts):
        die("turn 1 did not come back with a response id")
    emit("text", f"\n  Turn 2, for every order: “{d['turn2']}”")

    runs: dict[str, list[dict]] = {}
    for step, arm in ((5, "history"), (6, "chain")):
        a = ARMS[arm]
        banner(step, TOTAL, ("Packbot v1 (before the upgrade) — " if arm == "history" else "Packbot v2 (after the upgrade) — ")
               + a["label"].split(" · ")[1])
        note(a["how"])
        runs[arm] = second_turns(cli, world, arm, firsts, d)

    banner(7, TOTAL, "Why — whose notes MemoryRouter attached to turn 2")
    note("Claude gets the request body MemoryRouter saw on turn 2, through the same boundary, told to copy out its "
         "memory first. v1's body holds the conversation; v2's holds only the new message — the same sentence for "
         "all six orders")
    checks = check(cli, world, firsts, d) if cli.router_ok else []  # type: ignore[attr-defined]
    for row in checks:
        emit("text", show_check(row))
        emit("inspect", "", **row)
    if checks:
        n = sum(any(w not in (r["customer"], "?") for w in r["chain"]["whose"]) for r in checks)
        note(f"v2: MemoryRouter searched with “gate code … must not put in the box”, the only words in the request "
             f"body, and attached the same notes to every order — another customer's on {n} of {len(checks)}")

    banner(8, TOTAL, "Control and fix — still on previous_response_id")
    for arm in ("chain_bare", "chain_named"):
        emit("text", f"\n  {ARMS[arm]['label']}")
        note(ARMS[arm]["how"])
        runs[arm] = second_turns(cli, world, arm, firsts, d)
    note("without the boundary on turn 2 the stored chain still carries turn 1's memory: the wrong code came in "
         "with the second attachment")

    banner(9, TOTAL, "Scoreboard — the same six chats, the same model; only how turn 2 is sent differs")
    lines = [f"\n    {'order':<22}" + "".join(f"{ARMS[a]['short']:>13}" for a in ARMS)]
    rows = []
    for i, c in enumerate(custs):
        cells = {a: (runs[a][i]["grade"] or {}).get("ok") if runs[a][i]["grade"] else None for a in runs}
        rows.append(dict(customer=c["name"], order=c["order"], **cells))
        lines.append(f"    {c['name'] + ' ' + c['order']:<22}" + "".join(f"{mark(cells.get(a)):>13}" for a in ARMS))
    tot = {a: sum(1 for r in runs[a] if r["grade"] and r["grade"]["ok"]) for a in runs}
    lines.append(f"    {'':<22}" + "".join(f"{str(tot.get(a, 0)) + '/' + str(len(custs)):>13}" for a in ARMS))
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=rows, totals=tot)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(firsts=firsts, runs=runs, checks=checks, totals=tot, tokens=tok, stored=stored,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} input + {tok['completion']:,} "
                 f"output tokens, billed to your MemoryLake balance.\n"
                 f"  Turn-1 responses were stored with the provider (store: true); MemoryLake cannot delete them.\n"
                 f"  Every reply and request body: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_facts(cli: CLI) -> None:
    world = load_world(cli)
    facts = sorted(f["fact"] for f in list_facts(cli, world["project"]))
    emit("text", "\n".join(f"   - {t}" for t in facts))
    note(f"{len(facts)} facts behind boundary {world['boundary']}")


def ask_pair(cli: CLI, customer: str) -> dict:
    """One customer's two-turn chat, sent the v1 way and the v2 way (the web page's Try view)."""
    d = load_data()
    i = next((n for n, c in enumerate(d["customers"]) if c["name"] == customer), None)
    if i is None:
        die(f"unknown customer {customer!r}")
    world = load_world(cli)
    c = d["customers"][i]
    first = turn1(cli, world, i, c, d)
    out = dict(customer=customer, first=first)
    for arm in ("history", "chain", "chain_named"):
        r = turn2(cli, world, arm, i, c, d, first, echo=True)
        out[arm] = dict(reply=r["reply"] if r else "", grade=grade(c, d, r["reply"]) if r else None,
                        input_tokens=r and r["input_tokens"])
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
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = list the stored customer notes; "
                         "ask = one customer's chat, sent the v1 and v2 ways; cleanup = delete everything")
    ap.add_argument("--customer", default="Tomas Reyes", help="for ask: whose order the packer names")
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
        banner(2, 2, "What Packbot remembers")
        show_facts(cli)
        return
    if args.command == "ask":
        banner(2, 2, f"{args.customer}'s order — v1, v2, and v2 with the subject named")
        r = ask_pair(cli, args.customer)
        for arm in ("history", "chain", "chain_named"):
            emit("text", f"  {ARMS[arm]['label']}\n" + show_row(dict(customer=args.customer, **r[arm])))
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py ask --customer "Lena Vos"` or `python3 demo.py facts`; '
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

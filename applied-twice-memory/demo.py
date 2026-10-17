#!/usr/bin/env python3
"""applied-twice-memory — the rule is in memory and in the tool, and the model applies it before the tool does.

Quillhaven Bookkeeping (a fictional six-person firm) tuned five calculator tools in its custom GPT — overtime pay,
commission, mileage reimbursement, late fees, insurance payouts — and stores them once in MemoryLake as workflow
memory, so any model the team uses can call them. The firm's records (timesheets, bookings, a mileage log, an invoice,
a claim) and its policies (40-hour threshold, 50,000 dollar quota, 30 commuting miles, 10-day grace period) are in
memory too. Each tool applies its own threshold. A model that reads the policy takes the threshold off first, the tool
takes it off again, and the answer is exact to the cent and wrong.

The demo asks ten questions (eight from a record, two what-ifs) through MemoryRouter (`router chat --body … --boundary`)
with the tools read back from workflow memory, four ways with gpt-4o-mini and two with claude-haiku-4-5:
  · bare tools (parameter names, one-line descriptions), records + policies attached;
  · the same bare tools, records only attached (the control: the policies are not in the memory the model sees);
  · described tools: each parameter says TOTAL, as recorded, never subtract the threshold yourself;
  · tools that take the record id and read the recorded value from typed memory themselves.
Every number the model passes is checked against the record: ✓ as recorded, ⇣ threshold already taken off.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 450 calls per run, roughly 300,000 tokens).
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-atm"  # memorylake-usecases / applied-twice-memory


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
        if shown[:2] == ["fact", "add"] and len(shown) > 8:
            tail = shown[-2:] if shown[-2] == "--workspace" else []
            body = shown[:len(shown) - len(tail)]
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


MODEL = "gpt-4o-mini"        # the main model: router chat (OpenAI Chat Completions, with tools)
ARMS = ("bare", "nopolicy", "described", "record", "claude_bare", "claude_record")
KINDS = ("recorded", "whatif")
MAX_TOKENS = 300
ROUNDS = 4                   # model turns per answer: tool calls, then the reply


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "workflows.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v)


def plain(v) -> str:
    """A number as the records write it: 72,400 · 51.5 · null."""
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- the workflow tools (code, not the model)

def calc(tool: str, a: dict) -> float:
    """Quillhaven's five workflow tools. Each applies its own threshold: 40 hours, the 50,000 dollar quota, 30 commuting
    miles, a 10-day grace period, the 500 dollar deductible."""
    th = load_data()["thresholds"]
    f = lambda k: float(a[k])  # noqa: E731  (a missing key raises KeyError: "missing input")
    if tool == "overtime_pay":
        v = max(0.0, f("hours") - th["hours"]) * f("hourly_rate") * 1.5
    elif tool == "commission":
        v = max(0.0, f("bookings") - th["bookings"]) * 0.08
    elif tool == "mileage_reimbursement":
        v = max(0.0, f("miles") - th["miles"]) * 0.70
    elif tool == "late_fee":
        v = f("invoice_amount") * 0.0005 * max(0.0, f("days_overdue") - th["days_overdue"])
    elif tool == "insurance_payout":
        v = max(0.0, f("claimed") - th["claimed"])
    else:
        raise ValueError(f"no workflow tool called {tool!r}")
    return round(v + 1e-9, 2)


EXTRA = {"overtime_pay": ("extra_hours", "hours"), "commission": ("extra_bookings", "bookings")}


def record_inputs(rec: dict, extra: dict | None) -> dict:
    a = dict(rec["fields"])
    for k, v in (extra or {}).items():
        a[k] = a[k] + v
    return a


def expected(q: dict, recs: dict) -> float:
    rec = recs[q["record"]]
    return calc(q["tool"], record_inputs(rec, q.get("extra")))


# --------------------------------------------------------------------------- memory: read back

def list_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    items, token = [], None
    while True:
        args = ["fact", "list", "--projects", project, "--page-size", "200"]
        if token:
            args += ["--continuation-token", token]
        page_ = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items += page_.get("items") or []
        token = page_.get("continuation_token")
        if not token:
            break
    return items


def memory_view(cli: CLI, world: dict, echo: bool = False) -> dict:
    """Workflows (tool definitions per schema) and typed records, from the facts' metadata alone."""
    d = load_data()
    order = [t["function"]["name"] for t in d["schemas"]["bare"]]
    schemas: dict = {}
    for f in list_facts(cli, world["workflows"], echo=echo):
        m = f.get("metadata") or {}
        if m.get("kind") == "workflow" and m.get("tool"):
            schemas.setdefault(m["schema"], []).append(m["tool"])
    for s in schemas.values():
        s.sort(key=lambda t: order.index(t["function"]["name"]) if t["function"]["name"] in order else 99)
    recs = {}
    for f in list_facts(cli, world["records"], echo=echo):
        m = f.get("metadata") or {}
        if m.get("kind") == "record" and m.get("id"):
            recs[m["id"]] = dict(id=m["id"], tool=m.get("tool"), fields=m.get("fields") or {}, fact=f.get("id"), text=f.get("fact"))
    return dict(schemas=schemas, records=recs)


# --------------------------------------------------------------------------- step 2: setup

PROJECTS = ("workflows", "records", "policies")
SCOPES = {"full": ("records", "policies"), "records": ("records",)}


def find_project(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)


def demo_boundaries(cli: CLI, echo: bool = False) -> list[dict]:
    # `boundary get --by-custom-id` is not available: list and match the custom_id prefix.
    items, token = [], None
    while True:
        args = ["boundary", "list", "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page_ = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items += page_.get("items") or []
        token = page_.get("continuation_token")
        if not token:
            break
    return [b for b in items if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, k) for k in PROJECTS):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    co = d["company"]
    desc = {"workflows": f"The workflow tools {co}'s team tuned in its custom GPT, stored once as memory. Demo data.",
            "records": f"{co}'s client and staff records: timesheets, bookings, mileage, invoices, claims. Demo data.",
            "policies": f"{co}'s pay and billing policies. Demo data."}
    world: dict = {}
    for k in PROJECTS:
        p = cli.run("proj", "create", "--name", f"{co}: {k}", "--custom-id", cid(k), "--description", desc[k], scoped=True)
        world[k] = p["id"]
    names = {"full": "Bookkeeping assistant: records + policies", "records": "Bookkeeping assistant: records only"}
    for scope, keys in SCOPES.items():
        b = cli.run("boundary", "create", "--name", names[scope], "--custom-id", cid(scope),
                    "--projects", ",".join(world[k] for k in keys), scoped=True)
        world["b_" + scope] = b["id"]
    note("three projects: the workflow tools (read by the app, never attached), the records, the policies. Two "
         "boundaries: records + policies (every arm but one), records only (the control in step 7)")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    world: dict = {}
    for k in PROJECTS:
        p = find_project(cli, k)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world[k] = p["id"]
    for scope in SCOPES:
        if cid(scope) not in bs:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["b_" + scope] = bs[cid(scope)]
    return world


# --------------------------------------------------------------------------- steps 3–4: store

def add_typed(cli: CLI, project: str, texts: list[str], metas: list[dict]) -> list[str]:
    got = cli.run("fact", "add", "--project", project, *texts, scoped=True)
    facts = (got or {}).get("facts") or []
    if len(facts) != len(texts):
        die(f"`fact add` returned {len(facts)} facts for {len(texts)} texts")

    def one(k_fm):
        k, (f, m) = k_fm
        cli.run("fact", "update", f["id"], "--project", project, "--metadata", json.dumps(m), scoped=True, echo=k == 0)
        return f["id"]

    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, enumerate(zip(facts, metas))))


def workflow_text(schema: str, t: dict) -> str:
    fn = t["function"]
    params = "; ".join(f"{k} ({v.get('description', '')})" for k, v in fn["parameters"]["properties"].items())
    return f"Workflow tool {fn['name']}, {schema} version: {fn['description']} Parameters: {params}"


def store_workflows(cli: CLI, world: dict) -> None:
    d = load_data()
    texts, metas, shown = [], [], []
    for schema, tools in d["schemas"].items():
        for t in tools:
            texts.append(workflow_text(schema, t))
            metas.append(dict(kind="workflow", schema=schema, tool=t))
            fn = t["function"]
            shown.append(dict(schema=schema, name=fn["name"], description=fn["description"],
                              params={k: v.get("description", "") for k, v in fn["parameters"]["properties"].items()}))
    out = [f"\n  {d['company']}'s team tuned five calculator tools in its custom GPT. Each version is one memory, the "
           "tool definition (JSON schema) in its metadata:"]
    for s in d["schemas"]:
        for t in d["schemas"][s]:
            fn = t["function"]
            out.append(f"    {s:<10} {fn['name']}({', '.join(fn['parameters']['properties'])})")
    emit("text", "\n".join(out))
    add_typed(cli, world["workflows"], texts, metas)
    note(f"{len(texts)} tool definitions stored; the app reads them back with `fact list` and hands them to whichever "
         "model it calls — the same workflow memory for gpt-4o-mini (steps 6-9) and claude-haiku-4-5 (step 10)")
    emit("workflows", "", tools=shown)


def store_records(cli: CLI, world: dict) -> list[str]:
    d = load_data()
    out = ["\n  records (one fact each, typed fields in metadata):"]
    out += [f"    {r['id']:<11} {r['text']}" for r in d["records"]]
    out += ["  policies:"] + [f"    {p['id']:<11} {p['text']}" for p in d["policies"]]
    emit("text", "\n".join(out))
    ids = add_typed(cli, world["records"], [r["text"] for r in d["records"]],
                    [dict(kind="record", id=r["id"], tool=r["tool"], fields=r["fields"]) for r in d["records"]])
    add_typed(cli, world["policies"], [p["text"] for p in d["policies"]],
              [dict(kind="policy", id=p["id"], tool=p["tool"]) for p in d["policies"]])
    # A new fact is in `fact list` at once and in retrieval a few seconds later: wait until a question finds one.
    probe = "How many hours did Dana Ruiz work in the week of 5 October?"
    for attempt in range(12):
        res = cli.run("search", probe, "--projects", world["records"], "--types", "fact", "--top-k", "3",
                      scoped=True, echo=attempt == 0) or {}
        if any(h.get("id") in ids for h in res.get("facts") or []):
            note(f"retrieval finds them ({'at once' if attempt == 0 else f'after {attempt * 5} s'})")
            break
        time.sleep(5)
    else:
        note("retrieval does not return the new facts yet after 60 s; going on (MemoryRouter may attach less)")
    emit("records", "", records=d["records"], policies=d["policies"])
    return ids


# --------------------------------------------------------------------------- step 5: what memory gives

def show_key(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    view = memory_view(cli, world, echo=echo)
    missing = [s for s in d["schemas"] if len(view["schemas"].get(s) or []) != len(d["schemas"][s])]
    if missing or len(view["records"]) != len(d["records"]):
        die(f"memory read back incomplete: schemas {missing}, {len(view['records'])}/{len(d['records'])} records")
    th = d["thresholds"]
    lines = ["\n  what each tool needs, and the threshold the tool itself takes off (the policy, in code):"]
    for t in view["schemas"]["bare"]:
        fn = t["function"]
        cut = [f"{k} − {plain(th[k])}" for k in fn["parameters"]["properties"] if k in th]
        lines.append(f"    {fn['name']:<22} {', '.join(fn['parameters']['properties']):<28} {'; '.join(cut)}")
    lines.append("\n  the answer key, worked out by code from the recorded values:")
    key = []
    for q in d["questions"]:
        rec = view["records"][q["record"]]
        a = record_inputs(rec, q.get("extra"))
        v = calc(q["tool"], a)
        key.append(dict(q=q["id"], value=v, inputs=a))
        ins = ", ".join(f"{k} {plain(x)}" for k, x in a.items() if k != "deductible")
        lines.append(f"    {q['id']:<3} {q['say'][:70]:<70} {money(v):>9}   {q['tool']}({ins})")
    emit("text", "\n".join(lines))
    note("the records and the policies are both in the memory MemoryRouter attaches; the policy's threshold is also "
         "inside each tool — apply it before the call and it is applied twice")
    emit("key", "", key=key, thresholds=th)
    return view


# --------------------------------------------------------------------------- MemoryRouter

def router_call(cli: CLI, args: list[str], echo: bool) -> dict | None:
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
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
    return res


def count(cli: CLI, prompt: int, reply: int) -> None:
    with LOCK:
        cli.tokens["prompt"] += prompt  # type: ignore[attr-defined]
        cli.tokens["completion"] += reply  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]


BODY_SEQ = [0]


def body_file(body: dict, tag: str) -> str:
    """Each call gets its own request-body file (calls run in parallel; a shared file would be read half-written)."""
    with LOCK:
        BODY_SEQ[0] += 1
        n = BODY_SEQ[0]
    folder = OUT / "bodies"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{tag}-{n:04d}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, p)
    return os.path.relpath(p)


def chat_body(cli: CLI, body: dict, say: str | None, boundary: str | None, tag: str, echo: bool = False) -> dict | None:
    args = ["chat"] + ([say] if say else []) + ["--body", body_file(body, tag)] + (["--boundary", boundary] if boundary else [])
    res = router_call(cli, args, echo)
    if res:
        u = res.get("usage") or {}
        count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return res


def first_body(arm: str, view: dict) -> dict:
    d = load_data()
    a = d["arms"][arm]
    return {"model": a["model"], "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": d["system"]}],
            "tools": view["schemas"][a["schema"]], "tool_choice": "auto"}


def baselines(cli: CLI, arm: str, view: dict) -> dict:
    """Each question once without a boundary, the same request: those prompt tokens are the request alone, so a
    boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    qs = d["questions"]
    body = first_body(arm, view)

    def one(nq):
        r = chat_body(cli, body, nq[1]["say"], None, f"{arm}-base", echo=nq[0] == 0)
        return ((r or {}).get("usage") or {}).get("prompt_tokens", 0)
    with ThreadPoolExecutor(6) as pool:
        got = list(pool.map(one, enumerate(qs)))
    return {q["id"]: g for q, g in zip(qs, got)}


# --------------------------------------------------------------------------- running a tool call

ST = {"ok": "✓", "twice": "⇣", "delta": "Δ", "wrong": "✗"}


def audit(q: dict, name: str, args: dict, view: dict) -> list[dict]:
    """Each number the model passed, against the recorded value: ok · twice (the recorded value with the tool's own
    threshold already taken off) · delta (a what-if's change alone, not the new total) · wrong."""
    d = load_data()
    th = d["thresholds"]
    rec = view["records"][q["record"]]
    if name != q["tool"]:
        return [dict(field="tool", given=name, memory=q["tool"], status="wrong", note="another tool")]
    out = []
    want = record_inputs(rec, q.get("extra"))
    for k, given in args.items():
        if k not in want:
            continue
        truth = want[k]
        if same(given, truth):
            st = "ok"
        elif k in th and same(given, truth - th[k]):
            st = "twice"
        elif (q.get("extra") or {}).get(k) is not None and same(given, q["extra"][k]):
            st = "delta"
        else:
            st = "wrong"
        out.append(dict(field=k, given=given, memory=truth, status=st, threshold=th.get(k), record=rec["id"],
                        whatif=k in (q.get("extra") or {})))
    return out


def run_tool(schema: str, q: dict, name: str, args: dict, view: dict) -> tuple[dict, dict]:
    """bare / described: the numbers are whatever the model put in the call. record: the record id (and a what-if's
    change) come from the call, every number from typed memory."""
    rec_: dict = dict(name=name, args=args)
    try:
        if schema == "record":
            rid = " ".join(str(args.get("record") or "").upper().split())
            rec = view["records"].get(rid)
            if rec is None:
                raise LookupError(f"no record {args.get('record')!r} in memory")
            ek = EXTRA.get(name)
            extra = {ek[1]: float(args[ek[0]])} if ek and args.get(ek[0]) not in (None, 0, "") else {}
            inputs = record_inputs(rec, extra)
            rec_["read"] = dict(record=rec["id"], fact=rec["fact"], fields=rec["fields"], extra=extra)
            rec_["audit"] = ([] if rec["id"] == q["record"] else
                             [dict(field="record", given=rec["id"], memory=q["record"], status="wrong")])
            want_extra = q.get("extra") or {}
            for k in set(extra) | set(want_extra):
                if not same(extra.get(k, 0), want_extra.get(k, 0)):
                    rec_["audit"].append(dict(field=ek[0] if ek else k, given=extra.get(k, 0), memory=want_extra.get(k, 0),
                                              status="wrong"))
        else:
            inputs = dict(args)
            rec_["audit"] = audit(q, name, args, view)
        result = {"amount_dollars": calc(name, inputs)}
    except KeyError as e:
        result = {"error": f"missing input: {e.args[0]}"}
    except (LookupError, ValueError, TypeError) as e:
        result = {"error": str(e)}
    rec_["result"] = result
    return result, rec_


# --------------------------------------------------------------------------- one answer: the agent loop

ANSWER = re.compile(r"^\W*answer\W*:\s*(.+)$", re.I | re.M)
NO_RECORD = re.compile(r"no record|not (?:on record|in memory|available|recorded|found)|"
                       r"(?:don't|do not|doesn't|does not) have|unable to|cannot (?:be )?(?:determine|calculate|compute|provide)", re.I)
ZERO = re.compile(r"\bno (?:overtime|commission)\b|\bnot (?:eligible|entitled)\b|\bnone\b|\bzero\b|\bnothing\b", re.I)
NUMBER = re.compile(r"(?<![A-Za-z0-9-])\$?\s?(\d[\d,]*(?:\.\d+)?)")


def answer_line(reply: str) -> str:
    found = ANSWER.findall(reply or "")
    line = found[-1] if found else ((reply or "").strip().split("\n")[0] if (reply or "").strip() else "")
    return " ".join(line.replace("**", "").split())


def grade(exp: float, reply: str, calls: list[dict]) -> dict:
    """right = the amount the recorded values give · twice = wrong, and a call carried a value with the threshold
    already taken off · wrong = another amount · missed = no amount (“No record”, or still calling tools)."""
    line = answer_line(reply)
    if not line:
        return dict(clause=f"(no reply: still calling tools after {ROUNDS} turns)", verdict="missed")
    nums = [float(x.replace(",", "")) for x in NUMBER.findall(line) if x.replace(",", "").replace(".", "").isdigit()]
    if nums:
        ok = abs(nums[0] - float(exp)) < 0.011
    elif exp == 0 and ZERO.search(line):
        ok = True
    elif NO_RECORD.search(line):
        return dict(clause=line, verdict="missed")
    else:
        ok = False
    if ok:
        v = "right"
    elif any(x["status"] == "twice" for c in calls for x in c.get("audit") or []):
        v = "twice"
    else:
        v = "wrong"
    return dict(clause=line, verdict=v)


def ask(cli: CLI, world: dict, arm: str, q: dict, view: dict, exp: float, base: int = 0, echo: bool = False) -> dict:
    d = load_data()
    a = d["arms"][arm]
    body = first_body(arm, view)
    boundary = world["b_" + a["scope"]]
    msgs = list(body["messages"]) + [{"role": "user", "content": q["say"]}]
    calls: list[dict] = []
    first_tokens, reply = 0, ""
    for rnd in range(ROUNDS):
        b = dict(body, messages=msgs if rnd else body["messages"])
        res = chat_body(cli, b, None if rnd else q["say"], boundary, f"{arm}-{q['id']}", echo=echo and rnd == 0)
        if not res:
            return dict(q=q["id"], reply="", verdict=None, empty=False, clause="", calls=[], prompt_tokens=0)
        if rnd == 0:
            first_tokens = (res.get("usage") or {}).get("prompt_tokens", 0)
        msg = (res.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs:
            reply = (msg.get("content") or "").strip()
            break
        msgs.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg})
        for tc in tcs:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": fn.get("arguments")}
            result, rec = run_tool(a["schema"], q, fn.get("name") or "", args, view)
            calls.append(rec)
            msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
    out = dict(q=q["id"], reply=reply, prompt_tokens=first_tokens, calls=calls, **grade(exp, reply, calls))
    out["empty"] = bool(first_tokens and base and first_tokens <= base)
    out["twice"] = sorted({x["field"] for c in calls for x in c.get("audit") or [] if x["status"] == "twice"})
    return out


MARK = {"right": "✓", "twice": "⇣", "wrong": "✗", "missed": "–", None: "·"}
ORDER = ("twice", "wrong", "missed", "right", None)
VERDICTS = ("right", "twice", "wrong", "missed")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "")


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


def call_text(rec: dict) -> str:
    return rec["name"] + "(" + ", ".join(f"{k}={plain(v) if not isinstance(v, str) else v}" for k, v in rec["args"].items()) + ")"


def call_lines(rec: dict) -> list[str]:
    out = [f"        │ {call_text(rec)}"]
    if rec.get("read"):
        r = rec["read"]
        ex = "".join(f" + {plain(v)} {k} (what-if)" for k, v in (r.get("extra") or {}).items())
        out.append("        │   read from memory: " + r["record"] + " " +
                   ", ".join(f"{k} {plain(v)}" for k, v in r["fields"].items() if k != "deductible") + ex)
    parts = []
    for x in rec.get("audit") or []:
        s = f"{x['field']} {plain(x['given']) if not isinstance(x['given'], str) else x['given']} {ST[x['status']]}"
        if x["status"] == "twice":
            s += f" ({'the what-if total is' if x.get('whatif') else 'the record says'} {plain(x['memory'])}; {plain(x['memory'])} − {plain(x['threshold'])} = {plain(x['given'])}, and the tool takes {plain(x['threshold'])} off again)"
        elif x["status"] == "delta":
            s += f" (the change alone; the new total is {plain(x['memory'])})"
        elif x["status"] == "wrong":
            s += f" (should be {plain(x['memory']) if not isinstance(x['memory'], str) else x['memory']})"
        parts.append(s)
    if parts:
        out.append("        │   inputs vs memory: " + " · ".join(parts))
    res = rec["result"]
    out.append("        │   → " + (f"{money(res['amount_dollars'])} dollars" if "amount_dollars" in res else res.get("error", "")))
    return out


# --------------------------------------------------------------------------- steps 6–10: ask

def ask_all(cli: CLI, world: dict, arm: str, view: dict, exp: dict, base: dict) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    runs = d["arms"][arm]["runs"]
    one = lambda q, echo=False: ask(cli, world, arm, q, view, exp[q["id"]], base.get(q["id"], 0), echo)  # noqa: E731
    first = one(qs[0], True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(one, q) for n, q in enumerate(qs) for k in range(runs) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(runs)]
        pick = worst(answers)
        key = f"{money(exp[q['id']])} dollars"
        lines = [f"\n  {q['id']}  {q['say']}", f"      the record gives: {key}   ({q['record']})",
                 f"      answers {' '.join(shown(a) for a in answers)}   e.g. “{pick['clause'][:110]}”"]
        for rec in pick["calls"][:2]:
            lines += call_lines(rec)
        emit("text", "\n".join(lines))
        rows.append(dict(q=q["id"], qkind=q["kind"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], record=q["record"], key=key,
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], clause=a["clause"],
                           tokens=a["prompt_tokens"], twice=a["twice"],
                           calls=[dict(text=call_text(c), audit=c.get("audit"), read=c.get("read"), result=c["result"],
                                       lines=call_lines(c)[1:]) for c in a["calls"]]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for kind in KINDS:
        ans = [a for r in rows if r["qkind"] == kind for a in r["answers"] if a["verdict"]]
        t = {v: sum(a["verdict"] == v for a in ans) for v in VERDICTS}
        t["answers"] = len(ans)
        t["empty"] = sum(bool(a.get("empty")) for a in ans)
        t["twice_calls"] = sum(bool(a.get("twice")) for a in ans)
        # every call to the asked tool, by what the model put in it: as recorded, or with the threshold already off
        audits = [c.get("audit") or [] for a in ans for c in a.get("calls") or []]
        t["calls"] = len(audits)
        t["calls_twice"] = sum(1 for au in audits if any(x["status"] == "twice" for x in au))
        out[kind] = t
    return out


def tally_line(t: dict, kind: str) -> str:
    u = t[kind]
    if not u["answers"]:
        return "—"
    s = f"{u['right']}/{u['answers']} ✓"
    for v, m in (("twice", "⇣"), ("wrong", "✗"), ("missed", "–")):
        if u[v]:
            s += f" · {u[v]} {m}"
    return s


def applied_twice(rows: list[dict]) -> list[str]:
    """Every distinct input the model passed with the tool's threshold already taken off, and what the tool returned."""
    d = load_data()
    seen, out = set(), []
    for r in rows:
        for a in r["answers"]:
            for c in a.get("calls") or []:
                for x in c.get("audit") or []:
                    if x["status"] != "twice":
                        continue
                    key = (r["arm"], r["q"], x["field"], str(x["given"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    res = c["result"]
                    got = f"{money(res['amount_dollars'])}" if "amount_dollars" in res else res.get("error")
                    said = "the what-if total is" if x.get("whatif") else "the record says"
                    out.append(f"    {r['q']} [{d['arms'][r['arm']]['short']}] {x['field']}={plain(x['given'])}: {said} "
                               f"{plain(x['memory'])}, minus {plain(x['threshold'])} before the call and again in the tool "
                               f"→ {got} dollars")
    return out


def instead(rows: list[dict]) -> list[str]:
    """What the assistant answered instead of what the records give: one line per distinct reply, worst first."""
    order = ["twice", "wrong", "missed"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = a["clause"]
            if (r["arm"], r["q"], text[:120]) in seen:
                continue
            seen.add((r["arm"], r["q"], text[:120]))
            found.append((ARMS.index(r["arm"]), order.index(a["verdict"]),
                          f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:110]}”"))
    return [line_ for *_, line_ in sorted(found, key=lambda x: (x[1], x[0]))]


# --------------------------------------------------------------------------- pipeline

TOTAL = 11
LOCK = threading.Lock()
ARM_STEP = {"bare": 6, "nopolicy": 7, "described": 8, "record": 9, "claude_bare": 10, "claude_record": 10}
STEP_TITLE = {6: "bare tools, records + policies", 7: "bare tools, records only (the control)",
              8: "described tools, records + policies", 9: "tools that take the record id",
              10: "the same workflow memory, a second model (claude-haiku-4-5)"}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    if OUT.exists():
        shutil.rmtree(OUT / "bodies", ignore_errors=True)
    banner(2, TOTAL, "Set up — three projects, two boundaries")
    world = setup(cli)

    banner(3, TOTAL, "Workflow memory — the team's five tools, three versions each")
    store_workflows(cli, world)

    banner(4, TOTAL, f"Records and policies — {len(d['records'])} records, {len(d['policies'])} policies")
    store_records(cli, world)

    banner(5, TOTAL, "What memory gives — read back with `fact list`, worked out by code")
    view = show_key(cli, world)
    exp = {q["id"]: expected(q, view["records"]) for q in d["questions"]}

    rows: list[dict] = []
    tallies: dict = {}
    bases: dict = {}
    step = 0
    for arm in ARMS:
        a = d["arms"][arm]
        if ARM_STEP[arm] != step:
            step = ARM_STEP[arm]
            banner(step, TOTAL, "Ask the bookkeeping assistant — " + STEP_TITLE[step])
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tools = view["schemas"][a["schema"]]
        emit("text", f"\n  ── {a['short']} ({a['model']}, boundary: {'records + policies' if a['scope'] == 'full' else 'records only'})\n"
             + "\n".join(f"    │ {t['function']['name']}("
                         + ", ".join(f"{k}: {v.get('description', '')}" for k, v in t['function']['parameters']['properties'].items())
                         + ")" for t in tools[:2]) + f"\n    │ … {len(tools) - 2} more, read from workflow memory")
        bases[arm] = baselines(cli, arm, view)
        note(f"`router chat --body … --boundary` ({a['model']}, temperature 0, tools from workflow memory), each question "
             f"{a['runs']}×; the app runs every tool call and sends the result back. ✓ right · ⇣ wrong, a call carried a "
             "value with the threshold already taken off · ✗ wrong · – no amount · ∅ nothing attached")
        got = ask_all(cli, world, arm, view, exp, bases[arm])
        for r in got:
            r["key"] = exp[r["q"]]
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {a['short']}: " + " · ".join(f"{d['kinds'][k]} {tally_line(tallies[arm], k)}" for k in KINDS))
        emit("tally", "", arm=arm, **tallies[arm])

    banner(11, TOTAL, "Scoreboard")
    if tallies:
        emit("text", f"  {'':<38} {'from a record':<26} {'what-if':<22} tool calls, threshold already off")
        for arm in ARMS:
            if arm in tallies:
                t = tallies[arm]
                nc = sum(t[k]["calls"] for k in KINDS)
                ntw = sum(t[k]["calls_twice"] for k in KINDS)
                emit("text", f"  {d['arms'][arm]['short']:<38} {tally_line(t, 'recorded'):<26} {tally_line(t, 'whatif'):<22} {ntw}/{nc}")
        b, n, r_ = tallies.get("bare"), tallies.get("nopolicy"), tallies.get("record")
        if b and n:
            emit("text", f"\n  Same bare tools, policies taken out of the attached memory: from a record "
                         f"{b['recorded']['right']}/{b['recorded']['answers']} → {n['recorded']['right']}/{n['recorded']['answers']}"
                         f" (applied twice: {b['recorded']['twice']} → {n['recorded']['twice']}).")
        if r_:
            emit("text", f"  Tools that take the record id: from a record {r_['recorded']['right']}/{r_['recorded']['answers']}, "
                         f"what-if {r_['whatif']['right']}/{r_['whatif']['answers']}.")
    tw = applied_twice(rows)
    if tw:
        emit("text", "\n  inputs the model passed with the threshold already taken off — the tool took it off again:\n"
             + "\n".join(tw[:12]) + (f"\n    … {len(tw) - 12} more in out/report.json" if len(tw) > 12 else ""))
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant answered instead of what the records give (⇣ applied twice · ✗ wrong · – no amount · "
                     "∅ nothing attached):\n"
             + "\n".join(said[:16]) + (f"\n    … {len(said) - 16} more in out/report.json" if len(said) > 16 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, expected=exp, baselines=bases,
                   tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(OUT / "bodies", ignore_errors=True)
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and tool call: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:16], twice=tw[:12])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def workflows(cli: CLI) -> dict:
    """The workflow tools as memory holds them, and what each tool returns for the recorded value versus the value
    with its threshold already taken off."""
    d = load_data()
    world = load_world(cli)
    view = memory_view(cli, world, echo=True)
    th = d["thresholds"]
    out = []
    for i, t in enumerate(view["schemas"].get("bare") or []):
        name = t["function"]["name"]
        lines = [f"\n  {name}"]
        for s in ("bare", "described", "record"):
            fn = (view["schemas"].get(s) or [])[i]["function"]
            ps = fn["parameters"]["properties"]
            lines.append(f"    {s:<10} " + "; ".join(f"{k}: {v.get('description', '')}" for k, v in ps.items())[:150])
        for rec in sorted((r for r in view["records"].values() if r["tool"] == name), key=lambda r: r["id"]):
            f = rec["fields"]
            k = next(x for x in f if x in th)
            right = calc(name, f)
            pre = dict(f, **{k: f[k] - th[k]})
            twice = calc(name, pre)
            if right == twice:  # under the threshold: zero either way
                continue
            lines.append(f"    {rec['id']}: {k} {plain(f[k])} → {money(right)} dollars · {k} {plain(pre[k])} (already minus "
                         f"{plain(th[k])}) → {money(twice)} dollars")
            out.append(dict(tool=name, record=rec["id"], field=k, value=f[k], right=right, pre=pre[k], twice=twice))
        emit("text", "\n".join(lines))
    emit("workflows_view", "", rows=out)
    return dict(rows=out)


def ask_one(cli: CLI, text: str) -> dict:
    """One question with the record-id tools: the model names the record, the tool reads the values from memory."""
    world = load_world(cli)
    say = " ".join(text.split())
    if not say:
        die(f"give a question, e.g. \"{EXAMPLE}\"")
    cli.router_ok = True  # type: ignore[attr-defined]
    view = memory_view(cli, world, echo=True)
    q = dict(id="Q", kind="recorded", say=say, record=next(iter(view["records"])), tool="")
    a = ask(cli, world, "record", q, view, -1, 0, echo=True)
    for rec in a["calls"]:
        rec.pop("audit", None)
        emit("text", "\n".join(call_lines(rec)))
    emit("text", "\n  " + (a["reply"] or "(no reply)"))
    out = dict(say=say, reply=a["reply"], answer=answer_line(a["reply"]),
               calls=[dict(text=call_text(c), read=c.get("read"), result=c["result"], lines=call_lines(c)[1:]) for c in a["calls"]])
    emit("one", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    n_b = 0
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
        n_b += 1
    n_p, n_f = 0, 0
    for k in PROJECTS:
        proj = find_project(cli, k, echo=not quiet)
        if proj:
            n_f += len(list_facts(cli, proj["id"], echo=False))
            cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
            n_p += 1
    if not quiet:
        note(f"deleted {n_b} boundaries and {n_p} projects with their {n_f} fact(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()
    shutil.rmtree(OUT / "bodies", ignore_errors=True)


EXAMPLE = "How much should we reimburse Dana Ruiz for her October mileage?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "workflows", "cleanup"],
                    help="run = full demo (default); ask \"<question>\" = one question with the record-id tools; "
                         "workflows = the tools as memory holds them, and what a pre-applied threshold does; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question")
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
    if args.command == "workflows":
        banner(2, 2, "Workflows — the tools as memory holds them, and a threshold applied twice")
        workflows(cli)
        return
    if args.command == "ask":
        if not args.args:
            die(f"usage: python3 demo.py ask \"{EXAMPLE}\"")
        banner(2, 2, f"Ask — “{' '.join(args.args)}”: tools that take the record id")
        ask_one(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py workflows` or "
          "`python3 demo.py ask \"What late fee do we charge Marrowby Dental on invoice INV-2207?\"`; "
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

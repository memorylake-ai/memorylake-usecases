#!/usr/bin/env python3
"""tool-input-memory — hand the arithmetic to a tool, and the model starts making up the tool's inputs.

Vellumtide (a fictional API platform) runs a billing assistant: a tool-calling agent that, over two weeks, called
get_account, get_usage, get_contract and get_rate_card for four customers. Each tool result went into MemoryLake as
a fact with its typed fields as metadata ({tool, account, returned, fields}). Later the finance team asks it for
charges: "What is Halden Freight's October invoice total so far?"

The demo asks fourteen questions — four that look up a value, eight that compute a charge, two about an account
whose usage was never fetched — three ways, every call through MemoryRouter (`router chat --body … --boundary`):
  · the model reads the remembered results and does the arithmetic itself;
  · the model gets a compute_charge tool and copies the inputs from memory into the call — code does the arithmetic.
    The app checks every copied input against typed memory;
  · compute_charge takes only the account and the quantity and reads each input from typed memory itself (the latest
    result per field). A result that was never fetched is an error naming the tool.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 250 calls per run, roughly 100,000 tokens).
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

PREFIX = "mlu-tim"  # memorylake-usecases / tool-input-memory


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


MODEL = "gpt-4o-mini"        # answers: router chat (OpenAI Chat Completions, with tools)
RUNS = 3                     # answers per question per arm
ARMS = ("direct", "copied", "typed")       # the three ways of asking
KINDS = ("lookup", "computed", "missing")
MAX_TOKENS = 300
ROUNDS = 4                   # model turns per answer: tool calls, then the reply


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "billing.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v)


def plain(v) -> str:
    """A field value as the tool results write it: 1,362,480 · 12% · Scale · null."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


# --------------------------------------------------------------------------- the calculator (code, not the model)

class Missing(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def amount(quantity: str, inputs: dict, rates: dict, extra_seats=None) -> float:
    """Vellumtide's billing arithmetic. `inputs` holds tier / seats / api_calls / discount_pct; a needed input that is
    None raises Missing(field). Contract discounts apply to seats only; overage is billed per call."""
    d = load_data()
    need = list(d["needs"].get(quantity) or [])
    if not need:
        raise ValueError(f"unknown quantity {quantity!r}")
    for f in need:
        if inputs.get(f) is None:
            raise Missing(f)
    tier = str(inputs["tier"]).strip().lower()
    if tier not in ("growth", "scale"):
        raise Missing("tier")
    seat, included = rates[f"{tier}_seat"], rates[f"{tier}_included_calls"]

    def seats_cost(n) -> float:
        return float(n) * seat * (1 - float(inputs["discount_pct"]) / 100)

    def overage() -> float:
        return max(0, int(inputs["api_calls"]) - included) / 1000 * rates["overage_per_1000_calls"]

    if quantity == "seat_charge":
        v = seats_cost(inputs["seats"])
    elif quantity == "overage_charge":
        v = overage()
    elif quantity == "total":
        v = seats_cost(inputs["seats"]) + overage()
    else:  # added_seats_cost
        if extra_seats is None:
            raise Missing("extra_seats")
        v = seats_cost(extra_seats)
    return round(v + 1e-9, 2)


# --------------------------------------------------------------------------- typed memory: the tool results, read back

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


def typed_view(facts: list[dict]) -> dict:
    """From each fact's metadata {tool, account, name, returned, fields}: per account, per field, the latest result
    (and the ones it replaced); and the rate card."""
    accounts: dict = {}
    rates: dict = {}
    rate_from = None
    for f in facts:
        m = f.get("metadata") or {}
        fields = m.get("fields") or {}
        if m.get("tool") == "get_rate_card":
            if not rate_from or (m.get("returned") or "") >= rate_from["returned"]:
                rates, rate_from = dict(fields), dict(returned=m.get("returned"), fact=f.get("id"), tool="get_rate_card")
            continue
        acct = m.get("account")
        if not acct:
            continue
        a = accounts.setdefault(acct, dict(account=acct, name=m.get("name"), fields={}))
        for k, v in fields.items():
            entry = dict(value=v, tool=m.get("tool"), returned=m.get("returned") or "", fact=f.get("id"))
            cur = a["fields"].get(k)
            if cur is None:
                a["fields"][k] = dict(entry, replaced=[])
            elif entry["returned"] > cur["returned"]:
                a["fields"][k] = dict(entry, replaced=cur["replaced"] + [{x: cur[x] for x in ("value", "tool", "returned", "fact")}])
            else:
                cur["replaced"].append(entry)
    return dict(accounts=accounts, rates=rates, rate_from=rate_from)


def resolve(view: dict, account) -> dict | None:
    """An account by id (HAL-311) or name (Halden Freight), case-insensitive."""
    key = " ".join(str(account or "").lower().split())
    for a in view["accounts"].values():
        if key in (a["account"].lower(), (a["name"] or "").lower()):
            return a
    return None


def inputs_of(acct: dict | None) -> dict:
    return {k: v["value"] for k, v in (acct or {}).get("fields", {}).items()}


def expected(q: dict, view: dict):
    """The answer the remembered tool results give: a stored value, the calculator's amount, or None (no record)."""
    if q["kind"] == "lookup":
        return q["expect"]
    acct = resolve(view, q["account"])
    try:
        return amount(q["quantity"], inputs_of(acct), view["rates"], q.get("extra_seats"))
    except Missing:
        return None


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("tools"), "--by-custom-id", scoped=True, echo=echo)


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
    if demo_boundaries(cli) or find_project(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    p = cli.run("proj", "create", "--name", f"{d['company']} billing agent: tool results", "--custom-id", cid("tools"),
                "--description", f"Tool results the {d['company']} billing assistant remembered. Demo data.", scoped=True)
    b = cli.run("boundary", "create", "--name", "Billing assistant: tool results", "--custom-id", cid("tools"),
                "--projects", p["id"], scoped=True)
    world = dict(project=p["id"], boundary=b["id"])
    note("one project for the remembered tool results (facts) and one boundary over it: every model call in steps "
         "5-7 goes through `router chat … --boundary`")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    p = find_project(cli)
    if not p or cid("tools") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project=p["id"], boundary=bs[cid("tools")])


# --------------------------------------------------------------------------- step 3: the tool results go into memory

def store(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    rs = d["results"]
    out = [f"\n  {d['company']}'s {d['agent']} called four tools over two weeks; each result is one memory:"]
    for r in rs:
        out.append(f"    {r['returned']}  {r['tool']:<14} {r['account'] or '':<8} {r['text'].split(': ', 1)[1][:86]}")
    emit("text", "\n".join(out))
    got = cli.run("fact", "add", "--project", world["project"], *[r["text"] for r in rs], scoped=True)
    facts = (got or {}).get("facts") or []
    if len(facts) != len(rs):
        die(f"`fact add` returned {len(facts)} facts for {len(rs)} texts")
    note(f"{len(facts)} facts added; now each gets its typed fields as metadata (`fact update --metadata`), so a tool "
         "can read them back without parsing the text")

    def typed(k_fr):
        k, (f, r) = k_fr
        meta = dict(tool=r["tool"], account=r["account"], name=r["name"], returned=r["returned"], fields=r["fields"])
        cli.run("fact", "update", f["id"], "--project", world["project"], "--metadata", json.dumps(meta),
                scoped=True, echo=k == 0)
        return f["id"]

    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(typed, enumerate(zip(facts, rs))))
    # A new fact is in `fact list` at once and in retrieval a few seconds later: wait until a question finds one.
    probe = "How many seats does Halden Freight have?"
    for attempt in range(12):
        res = cli.run("search", probe, "--projects", world["project"], "--types", "fact", "--top-k", "3",
                      scoped=True, echo=attempt == 0) or {}
        if any(h.get("id") in ids for h in res.get("facts") or []):
            note(f"retrieval finds them ({'at once' if attempt == 0 else f'after {attempt * 5} s'})")
            break
        time.sleep(5)
    else:
        note("retrieval does not return the new facts yet after 60 s; going on (MemoryRouter may attach less)")
    view = [dict(id=i, tool=r["tool"], account=r["account"], name=r["name"], returned=r["returned"],
                 fields=r["fields"], text=r["text"]) for i, r in zip(ids, rs)]
    emit("memory", "", results=view)
    return view


# --------------------------------------------------------------------------- step 4: what typed memory says

def show_typed(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    view = typed_view(list_facts(cli, world["project"], echo=echo))
    rows = []
    out = [f"\n  {'account':<24} {'tier':<8} {'seats':<22} {'API calls (Oct)':<18} discount"]
    for acct in sorted(view["accounts"].values(), key=lambda a: a["account"]):
        f = acct["fields"]

        def cell(k):
            if k not in f:
                return f"— no {d['field_tool'][k]}"
            s = plain(f[k]["value"]) + ("%" if k == "discount_pct" else "")
            was = [x for x in f[k]["replaced"] if x["value"] != f[k]["value"]]
            if was:
                s += f" (was {', '.join(plain(x['value']) for x in was)} on {was[-1]['returned'][5:]})"
            return s
        out.append(f"  {acct['name'] + ' ' + acct['account']:<24} {cell('tier'):<8} {cell('seats'):<22} "
                   f"{cell('api_calls'):<18} {cell('discount_pct')}")
        rows.append(dict(account=acct["account"], name=acct["name"],
                         fields={k: dict(value=v["value"], tool=v["tool"], returned=v["returned"],
                                         replaced=[x["value"] for x in v["replaced"]]) for k, v in f.items()},
                         missing=[k for k in d["field_tool"] if k not in f]))
    r = view["rates"]
    out.append(f"\n  rate card ({view['rate_from']['returned'] if view['rate_from'] else '—'}): Growth {r.get('growth_seat')} "
               f"dollars/seat incl. {plain(r.get('growth_included_calls'))} calls · Scale {r.get('scale_seat')} dollars/seat "
               f"incl. {plain(r.get('scale_included_calls'))} calls · {r.get('overage_per_1000_calls')} dollars per 1,000 calls above")
    emit("text", "\n".join(out))
    exp = []
    lines = ["\n  what the remembered results give, worked out by code (the answer key for steps 5-7):"]
    for q in d["questions"]:
        v = expected(q, view)
        exp.append(dict(q=q["id"], value=v))
        shown_v = ("No record" if v is None else (money(v) + " dollars" if q["kind"] != "lookup" else plain(v)))
        lines.append(f"    {q['id']:<3} {q['say'][:66]:<66} {shown_v}")
    emit("text", "\n".join(lines))
    note("Halden Freight's seats changed between two get_account calls (37 → 41); Pellow & Finch never had get_usage "
         "called, so nothing in memory says how many API calls it made")
    emit("typed", "", rows=rows, rates=r, expected=exp)
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


def first_body(arm: str) -> dict:
    d = load_data()
    body = {"model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": d["system"][arm]}]}
    if arm != "direct":
        body["tools"] = d["tools"][arm]
        body["tool_choice"] = "auto"
    return body


def baselines(cli: CLI, arm: str) -> dict:
    """Each question once without a boundary, the same request: those prompt tokens are the request alone, so a
    boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    qs = d["questions"]
    body = first_body(arm)

    def one(nq):
        r = chat_body(cli, body, nq[1]["say"], None, f"{arm}-base", echo=nq[0] == 0)
        return ((r or {}).get("usage") or {}).get("prompt_tokens", 0)
    with ThreadPoolExecutor(6) as pool:
        got = list(pool.map(one, enumerate(qs)))
    return {q["id"]: g for q, g in zip(qs, got)}


# --------------------------------------------------------------------------- running a tool call

def audit(args: dict, view: dict) -> list[dict]:
    """Copied inputs against typed memory, for the inputs the quantity needs: ok · wrong (memory says otherwise) ·
    invented (memory has no such result) · dropped (null, memory has it)."""
    d = load_data()
    acct = resolve(view, args.get("account"))
    need = list(d["needs"].get(args.get("quantity")) or d["field_tool"].keys())
    out = []
    for k in need:
        given = args.get(k)
        m = (acct or {}).get("fields", {}).get(k)
        truth = m["value"] if m else None
        if given is None and truth is None:
            st = "ok"
        elif given is None:
            st = "dropped"
        elif truth is None:
            st = "invented"
        else:
            same = str(given).strip().lower() == str(truth).strip().lower()
            if not same:
                try:
                    same = abs(float(given) - float(truth)) < 1e-9
                except (TypeError, ValueError):
                    pass
            st = "ok" if same else "wrong"
        old = [x["value"] for x in (m or {}).get("replaced", [])]
        out.append(dict(field=k, given=given, memory=truth, status=st,
                        source=f"{m['tool']} {m['returned']}" if m else f"no {d['field_tool'][k]} result",
                        stale=st == "wrong" and given in old))
    return out


def run_tool(arm: str, args: dict, view: dict) -> tuple[dict, dict]:
    """compute_charge. copied: the inputs are whatever the model put in the call. typed: the account and quantity
    come from the call, every input from typed memory. Returns (what the tool sends back, a record for the demo)."""
    d = load_data()
    q = args.get("quantity")
    rec: dict = dict(args=args, arm=arm)
    acct = resolve(view, args.get("account"))
    try:
        if arm == "copied":
            inputs = {k: args.get(k) for k in d["field_tool"]}
            rec["audit"] = audit(args, view)
        else:
            if acct is None:
                raise LookupError(f"no tool results on record for account {args.get('account')}")
            inputs = inputs_of(acct)
            need = d["needs"].get(q) or []
            rec["read"] = [dict(field=k, value=acct["fields"][k]["value"], tool=acct["fields"][k]["tool"],
                                returned=acct["fields"][k]["returned"], fact=acct["fields"][k]["fact"])
                           for k in need if k in acct["fields"]]
        v = amount(q, inputs, view["rates"], args.get("extra_seats"))
        result = {"amount_dollars": v}
        if arm == "typed":
            result["inputs"] = {r["field"]: f"{plain(r['value'])} ({r['tool']}, {r['returned']})" for r in rec["read"]}
    except Missing as e:
        if arm == "typed":
            tool = d["field_tool"].get(e.field, "a tool")
            result = {"error": f"not on record: no {tool} result for {acct['account']} ({acct['name']}), so "
                               f"{e.field} is unknown"}
        else:
            result = {"error": f"missing input: {e.field}"}
    except (LookupError, ValueError, TypeError) as e:
        result = {"error": str(e)}
    rec["result"] = result
    return result, rec


# --------------------------------------------------------------------------- one answer: the agent loop

ANSWER = re.compile(r"^\W*answer\W*:\s*(.+)$", re.I | re.M)
NO_RECORD = re.compile(r"no record|not (?:on record|in memory|available|recorded|found)|"
                       r"(?:don't|do not|doesn't|does not) have|no (?:get_usage|usage|data|information)\b|"
                       r"unable to|cannot (?:be )?(?:determine|calculate|compute|provide)", re.I)
NUMBER = re.compile(r"(?<![A-Za-z0-9-])\$?\s?(\d[\d,]*(?:\.\d+)?)")


def answer_line(reply: str) -> str:
    found = ANSWER.findall(reply or "")
    line = found[-1] if found else ((reply or "").strip().split("\n")[0] if (reply or "").strip() else "")
    return " ".join(line.replace("**", "").split())


def grade(q: dict, exp, reply: str) -> dict:
    """right = what the remembered results give · wrong = another value · missed = “No record” although memory has it ·
    invented = a value where memory has none."""
    line = answer_line(reply)
    if not line:  # still calling tools when the round limit came: no answer at all
        return dict(clause=f"(no reply: still calling tools after {ROUNDS} turns)", verdict="missed")
    said_none = bool(NO_RECORD.search(line))
    if exp is None:
        v = "right" if said_none else "invented"
    elif said_none:
        v = "missed"
    elif isinstance(exp, str):
        v = "right" if re.search(rf"\b{re.escape(exp)}\b", line, re.I) else "wrong"
    else:
        nums = [float(x.replace(",", "")) for x in NUMBER.findall(line) if x.replace(",", "").replace(".", "").isdigit()]
        v = "right" if nums and abs(nums[0] - float(exp)) < 0.011 else "wrong"
    return dict(clause=line, verdict=v)


def ask(cli: CLI, world: dict, arm: str, q: dict, view: dict, exp, base: int = 0, echo: bool = False) -> dict:
    body = first_body(arm)
    msgs = list(body["messages"]) + [{"role": "user", "content": q["say"]}]
    calls: list[dict] = []
    first_tokens, reply = 0, ""
    for rnd in range(ROUNDS):
        b = dict(body, messages=msgs if rnd else body["messages"])
        res = chat_body(cli, b, None if rnd else q["say"], world["boundary"], f"{arm}-{q['id']}", echo=echo and rnd == 0)
        if not res:
            return dict(q=q["id"], reply="", verdict=None, empty=False, clause="", calls=[], prompt_tokens=0)
        if rnd == 0:
            first_tokens = (res.get("usage") or {}).get("prompt_tokens", 0)
        msg = (res.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs or arm == "direct":
            reply = (msg.get("content") or "").strip()
            break
        msgs.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg})
        for tc in tcs:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": fn.get("arguments")}
            result, rec = run_tool(arm, args, view)
            calls.append(rec)
            msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
    out = dict(q=q["id"], reply=reply, prompt_tokens=first_tokens, calls=calls, **grade(q, exp, reply))
    out["empty"] = bool(first_tokens and base and first_tokens <= base)
    out["made_up"] = sorted({a["field"] for c in calls for a in c.get("audit", []) if a["status"] == "invented"})
    out["off"] = sorted({a["field"] for c in calls for a in c.get("audit", []) if a["status"] in ("wrong", "dropped")})
    return out


MARK = {"right": "✓", "wrong": "✗", "missed": "–", "invented": "✎", None: "·"}
ORDER = ("wrong", "invented", "missed", "right", None)
VERDICTS = ("right", "wrong", "missed", "invented")
ST = {"ok": "✓", "wrong": "✗", "invented": "✎", "dropped": "∅"}


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "")


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: (ORDER.index(a["verdict"]), -len(a.get("made_up") or [])))[0]


def call_text(rec: dict) -> str:
    a = rec["args"]
    keys = ["quantity", "account"] + [k for k in ("tier", "seats", "api_calls", "discount_pct", "extra_seats") if k in a]
    return "compute_charge(" + ", ".join(f"{k}={plain(a.get(k))}" for k in keys) + ")"


def call_lines(rec: dict) -> list[str]:
    out = [f"        │ {call_text(rec)}"]
    if rec.get("audit"):
        parts = []
        for x in rec["audit"]:
            s = f"{x['field']} {plain(x['given'])} {ST[x['status']]}"
            if x["status"] == "invented":
                s += f" (memory: {x['source']})"
            elif x["status"] in ("wrong", "dropped"):
                s += f" (memory: {plain(x['memory'])}, {x['source']}{'; the replaced value' if x['stale'] else ''})"
            parts.append(s)
        out.append("        │   inputs vs memory: " + " · ".join(parts))
    if rec.get("read") is not None:
        out.append("        │   read from memory: " + (" · ".join(
            f"{r['field']} {plain(r['value'])} ({r['tool']} {r['returned']})" for r in rec["read"]) or "nothing"))
    res = rec["result"]
    out.append("        │   → " + (f"{money(res['amount_dollars'])} dollars" if "amount_dollars" in res else res.get("error", "")))
    return out


# --------------------------------------------------------------------------- steps 5–7: ask

def ask_all(cli: CLI, world: dict, arm: str, view: dict, exp: dict, base: dict) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    one = lambda q, echo=False: ask(cli, world, arm, q, view, exp[q["id"]], base.get(q["id"], 0), echo)
    first = one(qs[0], True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(one, q) for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        v = exp[q["id"]]
        key = "No record" if v is None else (plain(v) if q["kind"] == "lookup" else f"{money(v)} dollars")
        lines = [f"\n  {q['id']}  {q['say']}", f"      memory gives: {key}   ({q['fact']})",
                 f"      answers {' '.join(shown(a) for a in answers)}   e.g. “{pick['clause'][:120]}”"]
        if pick["calls"]:
            for rec in pick["calls"][:2]:
                lines += call_lines(rec)
        emit("text", "\n".join(lines))
        rows.append(dict(q=q["id"], qkind=q["kind"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], fact=q["fact"], key=key,
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], clause=a["clause"],
                           tokens=a["prompt_tokens"], made_up=a["made_up"], off=a["off"],
                           calls=[dict(text=call_text(c), audit=c.get("audit"), read=c.get("read"), result=c["result"])
                                  for c in a["calls"]]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for kind in KINDS:
        ans = [a for r in rows if r["qkind"] == kind for a in r["answers"] if a["verdict"]]
        t = {v: sum(a["verdict"] == v for a in ans) for v in VERDICTS}
        t["answers"] = len(ans)
        t["empty"] = sum(bool(a.get("empty")) for a in ans)
        t["made_up"] = sum(bool(a.get("made_up")) for a in ans)
        t["tool_used"] = sum(bool(a.get("calls")) for a in ans)
        out[kind] = t
    return out


def tally_line(t: dict, kind: str) -> str:
    u = t[kind]
    if not u["answers"]:
        return "—"
    s = f"{u['right']}/{u['answers']} ✓"
    for v, m in (("wrong", "✗"), ("missed", "–"), ("invented", "✎")):
        if u[v]:
            s += f" · {u[v]} {m}"
    return s


def instead(rows: list[dict]) -> list[str]:
    """What the assistant answered instead of what memory gives: one line per distinct reply, worst first."""
    order = ["invented", "wrong", "missed"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = a["clause"]
            if (r["arm"], r["q"], text[:120]) in seen:
                continue
            seen.add((r["arm"], r["q"], text[:120]))
            extra = f"  [made-up input: {', '.join(a['made_up'])}]" if a.get("made_up") else ""
            found.append((ARMS.index(r["arm"]), order.index(a["verdict"]),
                          f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:110]}”{extra}"))
    return [line_ for *_, line_ in sorted(found, key=lambda x: (x[1], x[0]))]


def made_up_inputs(rows: list[dict]) -> list[str]:
    """Every tool call in which the model filled an input that memory does not have."""
    seen, out = set(), []
    for r in rows:
        for a in r["answers"]:
            for c in a.get("calls") or []:
                for x in c.get("audit") or []:
                    if x["status"] != "invented":
                        continue
                    key = (r["q"], x["field"], str(x["given"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    res = c["result"]
                    got = f"{money(res['amount_dollars'])} dollars" if "amount_dollars" in res else res.get("error")
                    out.append(f"    {r['q']} {x['field']}={plain(x['given'])} for {c['args'].get('account')} — memory: "
                               f"{x['source']} → the calculator returned {got}")
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
LOCK = threading.Lock()
ARM_STEP = {"direct": 5, "copied": 6, "typed": 7}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    if OUT.exists():
        shutil.rmtree(OUT / "bodies", ignore_errors=True)
    banner(2, TOTAL, "Set up — a project for the tool results, and a boundary over it")
    world = setup(cli)

    banner(3, TOTAL, f"The agent's memory — {len(d['results'])} tool results")
    store(cli, world)

    banner(4, TOTAL, "Typed memory — the latest result per account and field, read back with `fact list`")
    view = show_typed(cli, world)
    exp = {q["id"]: expected(q, view) for q in d["questions"]}

    rows: list[dict] = []
    tallies: dict = {}
    bases: dict = {}
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the billing assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        emit("text", "\n  system prompt, as the app sends it on every call:\n"
             + "\n".join("    │ " + x for x in d["system"][arm].splitlines()))
        if arm != "direct":
            fn = d["tools"][arm][0]["function"]
            props = ", ".join(fn["parameters"]["properties"])
            emit("text", f"  tool offered to the model: {fn['name']}({props})\n    │ {fn['description']}")
        note("first, each question once without a boundary: those prompt tokens are the request alone, so a boundary "
             "call with the same count had nothing attached (∅)")
        bases[arm] = baselines(cli, arm)
        note(f"`router chat --body … --boundary` ({MODEL}, temperature 0), each question {RUNS}×"
             + ("; the app runs each compute_charge call and sends the result back" if arm != "direct" else "")
             + ". ✓ right · ✗ wrong · – no amount although memory has one (“No record”, or no reply) · ✎ a value where memory has none · ∅ nothing attached")
        got = ask_all(cli, world, arm, view, exp, bases[arm])
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: " + " · ".join(
            f"{d['kinds'][k]} {tally_line(tallies[arm], k)}" for k in KINDS))
        if arm == "copied":
            mu = sum(tallies[arm][k]["made_up"] for k in KINDS)
            if mu:
                emit("text", f"    answers whose tool call carried an input memory does not have: {mu}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(8, TOTAL, "Scoreboard")
    if tallies:
        emit("text", f"  {'':<40} {'look up a value':<18} {'compute a charge':<22} an input never fetched")
        for arm in ARMS:
            if arm in tallies:
                t = tallies[arm]
                emit("text", f"  {d['arms'][arm]['short']:<40} {tally_line(t, 'lookup'):<18} {tally_line(t, 'computed'):<22} "
                             f"{tally_line(t, 'missing')}")
        a, c, ty = tallies.get("direct"), tallies.get("copied"), tallies.get("typed")
        if a and c:
            emit("text", f"\n  Moving the arithmetic into a tool: charges {a['computed']['right']}/{a['computed']['answers']} → "
                         f"{c['computed']['right']}/{c['computed']['answers']}. The account with no usage on record: "
                         f"{a['missing']['right']}/{a['missing']['answers']} → {c['missing']['right']}/{c['missing']['answers']}.")
        if ty:
            emit("text", f"  The tool reading typed memory itself: charges {ty['computed']['right']}/{ty['computed']['answers']}, "
                         f"no usage on record {ty['missing']['right']}/{ty['missing']['answers']}.")
    mu = made_up_inputs([r for r in rows if r["arm"] == "copied"])
    if mu:
        emit("text", "\n  inputs the model put into compute_charge that memory does not have (step 6):\n"
             + "\n".join(mu[:10]) + (f"\n    … {len(mu) - 10} more in out/report.json" if len(mu) > 10 else ""))
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant answered instead of what memory gives (✎ invented · ✗ wrong · – “No record” · "
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
         instead=said[:16], made_up=mu[:10])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def bill(cli: CLI, which: str = "") -> dict:
    """Typed memory, and what the calculator makes of it, for one account or all."""
    d = load_data()
    world = load_world(cli)
    view = typed_view(list_facts(cli, world["project"], echo=True))
    accts = [resolve(view, which)] if which else sorted(view["accounts"].values(), key=lambda a: a["account"])
    if which and accts[0] is None:
        die(f"no tool results on record for {which!r}; try one of: "
            + ", ".join(a["account"] for a in view["accounts"].values()))
    out = []
    for acct in accts:
        lines = [f"\n  {acct['name']} ({acct['account']})"]
        for k in d["field_tool"]:
            f = acct["fields"].get(k)
            if f:
                was = f"   replaced {', '.join(plain(x['value']) + ' (' + x['returned'] + ')' for x in f['replaced'])}" if f["replaced"] else ""
                lines.append(f"    {k:<13} {plain(f['value']):<11} {f['tool']} {f['returned']}  {f['fact']}{was}")
            else:
                lines.append(f"    {k:<13} {'—':<11} no {d['field_tool'][k]} result on record")
        charges = {}
        for qty in ("seat_charge", "overage_charge", "total"):
            try:
                charges[qty] = amount(qty, inputs_of(acct), view["rates"])
                lines.append(f"    {qty:<15} {money(charges[qty])} dollars")
            except Missing as e:
                charges[qty] = None
                lines.append(f"    {qty:<15} not on record ({e.field} unknown: no {d['field_tool'].get(e.field)} result)")
        emit("text", "\n".join(lines))
        out.append(dict(account=acct["account"], name=acct["name"], charges=charges))
    emit("bill", "", accounts=out)
    return dict(accounts=out)


def ask_one(cli: CLI, text: str) -> dict:
    """One question the third way: the model calls compute_charge with the account; the tool reads typed memory."""
    d = load_data()
    world = load_world(cli)
    say = " ".join(text.split())
    if not say:
        die(f"give a question, e.g. \"{EXAMPLE}\"")
    cli.router_ok = True  # type: ignore[attr-defined]
    view = typed_view(list_facts(cli, world["project"], echo=True))
    q = dict(id="Q", kind="lookup", say=say, expect="")
    a = ask(cli, world, "typed", q, view, None, 0, echo=True)
    for rec in a["calls"]:
        emit("text", "\n".join(call_lines(rec)))
    emit("text", "\n  " + (a["reply"] or "(no reply)"))
    out = dict(say=say, reply=a["reply"], answer=answer_line(a["reply"]),
               calls=[dict(text=call_text(c), read=c.get("read"), result=c["result"]) for c in a["calls"]])
    emit("one", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    n_b = 0
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
        n_b += 1
    n_f = 0
    proj = find_project(cli, echo=not quiet)
    if proj:
        n_f = len(list_facts(cli, proj["id"], echo=False))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
    if not quiet:
        note(f"deleted {n_b} boundary and {'the project with its ' + str(n_f) + ' fact(s)' if proj else 'no project'}")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()
    shutil.rmtree(OUT / "bodies", ignore_errors=True)


EXAMPLE = "What is Halden Freight's October invoice total so far?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "bill", "cleanup"],
                    help="run = full demo (default); ask \"<question>\" = one question the third way (the tool reads "
                         "typed memory); bill [account] = typed memory and what the calculator makes of it; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question · for bill: an account id or name")
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
    if args.command == "bill":
        banner(2, 2, "Bill — typed memory per account, and what the calculator makes of it")
        bill(cli, " ".join(args.args))
        return
    if args.command == "ask":
        if not args.args:
            die(f"usage: python3 demo.py ask \"{EXAMPLE}\"")
        banner(2, 2, f"Ask — “{' '.join(args.args)}”: the tool reads typed memory")
        ask_one(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py bill \"Pellow & Finch\"` or "
          "`python3 demo.py ask \"If Halden Freight adds 3 seats, how much more will it pay per month?\"`; "
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

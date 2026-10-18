#!/usr/bin/env python3
"""provisioned-tenant-memory — the tenant's context went into the project description; extraction never reads it.

Minutely (a fictional AI meeting-notes SaaS) gives every customer team its own MemoryLake project, created by its
provisioning code: the project is named after the account id, and what the customer typed at signup ("our AI team's
weekly eval sync — keep every metric reading we say") has to go somewhere. Quillfold's three eval syncs (12 metric
readings, 3 action items) go into three projects provisioned three ways:

  A  the project only — name = account id
  B  + the signup note as the project's `--description`
  C  + the same signup note as the project's `fact instruction`

The three projects have the same name, and the same conversations go into each. Extraction often drops a whole
meeting of bare readings ("Recall at 10 for the new reranker came in at 0.81."); the description does not change
that — it is never read — and the fact instruction does. Then Quillfold's meeting assistant is asked for each reading
through MemoryRouter, once per tenant: a number, Unknown, or a number memory does not hold.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 85 calls on gpt-4o-mini per run).
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ptm"  # memorylake-usecases / provisioned-tenant-memory


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


# --------------------------------------------------------------------------- settings

MODEL = "gpt-4o-mini"
TEMPERATURE = "0"     # the most repeatable setting: what changes between answers is what memory holds
RUNS = 2              # answers per question and tenant
MAX_TOKENS = 60
COPIES = ("plain", "described", "instructed")   # the three tenant projects
ARMS = ("A", "B", "C")
ARM_COPY = {"A": "plain", "B": "described", "C": "instructed"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
ARM_LABEL = {"A": "A  project only", "B": "B  + description", "C": "C  + fact instruction"}
MARK = {"ok": "✓", "unknown": "?", "made": "✎", "other": "–"}
GRADE_NAME = {"ok": "the reading", "unknown": "Unknown", "made": "a number memory does not hold", "other": "no number"}


# --------------------------------------------------------------------------- objects

def find_project(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=echo)


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


def actor_facts(cli: CLI, actor: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--actors", actor, "--page-size", "200"], echo)


def project_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--projects", project, "--page-size", "200"], echo)


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type. Pass it when accepted.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def make_actor(cli: CLI, key: str, display: str, description: str, echo: bool, actor_type: str = "HUMAN") -> str:
    a = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", display, "--description", description,
                "--tags", "demo", *actor_type_args(cli, actor_type), echo=echo)
    cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=echo)
    return a["id"]


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()


def router(cli: CLI, prompt: str, system: str, boundary_id: str | None = None, max_tokens: int = MAX_TOKENS,
           echo: bool = False) -> dict | None:
    """One `router chat` call. Without a boundary it is the plain model call — the baseline for ∅."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["chat", prompt, "--model", MODEL, "--max-tokens", str(max_tokens), "--temperature", TEMPERATURE, "--system", system]
    if boundary_id:
        args += ["--boundary", boundary_id]
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model replies below are skipped; the memory steps still ran. Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    with LOCK:
        cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
        cli.tokens["completion"] += ct  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=(res["choices"][0]["message"]["content"] or "").strip(), prompt_tokens=pt)


BASE: dict = {}


def baseline(cli: CLI, prompt: str, system: str, echo: bool = False) -> int:
    """Prompt tokens of the same call without a boundary: a boundary call with exactly this many had nothing attached."""
    k = (prompt, system)
    if k not in BASE:
        r = router(cli, prompt, system, max_tokens=1, echo=echo)
        BASE[k] = r["prompt_tokens"] if r else 0
    return BASE[k]


# --------------------------------------------------------------------------- conversations

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, text: str, echo: bool) -> None:
    for attempt in range(5):
        try:
            cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                    "--text", text, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))





# --------------------------------------------------------------------------- waiting for extraction

def cooked(cli: CLI, conv: str, echo: bool, limit: int = 300) -> bool:
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
            first = False
            if st.get("cook_finished"):
                return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(5)
    return False

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
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: delete_fact(cli, f["id"], ["--actor", actor_id]), left))
        left = actor_facts(cli, actor_id)
        if not left:
            break
        time.sleep(2 * (attempt + 1))
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "tenant.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [f"{c}-{p['key']}" for c in COPIES for p in d["people"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def at(m: dict) -> datetime:
    return datetime.fromisoformat(m["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def readings(d: dict) -> list[dict]:
    return [x for x in d["lines"] if x["kind"] == "reading"]


def meeting_of(d: dict, mid: str) -> dict:
    return next(m for m in d["meetings"] if m["id"] == mid)


# --------------------------------------------------------------------------- grading

NOT_KNOWN = re.compile(r"\bunknown\b|\bnot (?:been )?(?:remembered|recorded|reported|available|mentioned|specified|known|provided|stored|found|"
                       r"in (?:the )?memory)\b|\bno (?:record|information|data|mention)\b|\bdon'?t (?:know|have)\b|\bdoes not (?:have|include|contain)\b|"
                       r"\bisn'?t (?:remembered|recorded|available)\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")
DATES = re.compile(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}\b|"
                   r"\b20\d\d-\d\d-\d\d\b|\b20\d\d\b", re.I)


def holds(x: dict, text: str) -> bool:
    """Does this text carry the line? A reading: its number. An action item: its topic."""
    if x["kind"] == "reading":
        return bool(re.search(x["value_re"], DATES.sub(" ", text), re.I))
    return bool(re.search(x["topic_re"], text, re.I))


def numbers(x: dict, reply: str) -> list[str]:
    """Numbers in the reply other than dates and the ones the question itself says (the 10 of "recall at 10")."""
    asked = set(NUMBER.findall(x.get("ask", "")))
    return [n for n in NUMBER.findall(DATES.sub(" ", reply)) if n not in asked]


def grade(x: dict, reply: str) -> str:
    if holds(x, reply):
        return "ok"
    if numbers(x, reply):
        return "made"
    if NOT_KNOWN.search(reply):
        return "unknown"
    return "other"


GRADER_CASES = [
    ("reranker", "The new reranker achieved a recall at 10 of 0.81.", "ok"),
    ("reranker", "Recall@10 for the new reranker was .81 on 1 October 2026.", "ok"),
    ("reranker", "Unknown.", "unknown"),
    ("reranker", "The new reranker got a recall at 10 of 0.85.", "made"),
    ("reranker", "The recall at 10 is not remembered.", "unknown"),
    ("latency", "p95 latency on the smaller model was 1,140 milliseconds.", "ok"),
    ("latency", "It was 1.14 s at p95.", "ok"),
    ("latency", "p95 latency is not recorded in memory.", "unknown"),
    ("longtask", "Task success on long sessions was 81 percent.", "made"),
    ("longtask", "Task success was 79% on long sessions.", "ok"),
    ("ticketcost", "Cost per resolved ticket was $0.38.", "ok"),
    ("ticketcost", "Each resolved ticket cost 38 cents (15 October 2026).", "ok"),
    ("context", "Unknown — the meetings did not record it.", "unknown"),
    ("context", "Context window utilization came in at 17 percent on Oct 15.", "made"),
    ("escalation", "Escalation precision was 0.68 on 15 Oct 2026.", "ok"),
    ("grounded", "I don't have information about the hallucination rate.", "unknown"),
    ("compaction", "Token cost per session with compaction was 2.6 cents.", "ok"),
    ("compaction", "The token cost with the compaction job was 26 cents.", "made"),
]


def check_grader(d: dict) -> None:
    byid = {x["id"]: x for x in d["lines"]}
    bad = [(i, r, want, grade(byid[i], r)) for i, r, want in GRADER_CASES if grade(byid[i], r) != want]
    if bad:
        die("grader self-check failed: " + "; ".join(f"{i}: {r!r} → {got}, want {want}" for i, r, want, got in bad))


# --------------------------------------------------------------------------- step 2: provision three tenants

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    emit("text", f"\n    {d['org']} signs up for {d['saas']}. Account {d['account']}. At signup they wrote:")
    emit("text", wrap(f"“{d['signup_note']}”", "      ", 104))
    emit("text", f"\n    {d['saas']}'s provision_tenant() creates the tenant's project — three ways, one per tenant copy:")
    for a in ARMS:
        emit("text", f"      {ARM_LABEL[a]}")
    for n, c in enumerate(COPIES):
        a = ARM_OF[c]
        emit("text", f"\n    {ARM_LABEL[a]}")
        # The same name for all three — the account id, as provisioning code names it. What differs is in the
        # custom_id and the boundary's name, never in the project's name (it is context for extraction).
        args = ["proj", "create", "--name", d["account"], "--custom-id", cid(c)]
        if c == "described":
            args += ["--description", d["signup_note"]]
        world["projects"][c] = cli.run(*args, scoped=True)["id"]
        if c == "instructed":
            cli.run("fact", "instruction", "set", "--project", world["projects"][c], "--text", d["signup_note"], scoped=True)
    emit("text", "\n    Read back — the signup note is stored in both B and C:")
    p = cli.run("proj", "get", world["projects"]["described"], scoped=True)
    emit("text", wrap(f"B  description: “{p.get('description')}”", "      ", 108))
    fi = cli.run("fact", "instruction", "get", "--project", world["projects"]["instructed"], scoped=True)
    emit("text", wrap(f"C  fact_instruction: “{(fi or {}).get('fact_instruction')}”", "      ", 108))
    emit("provisioned", "", account=d["account"], note_text=d["signup_note"], description=p.get("description"),
         instruction=(fi or {}).get("fact_instruction"))
    n = 0
    for c in COPIES:
        for pp in d["people"]:
            world["actors"][f"{c}-{pp['key']}"] = make_actor(cli, f"{c}-{pp['key']}", pp["display"], f"{pp['role']}. Demo data.",
                                                             echo=n == 0)
            n += 1
    note(f"{len(d['people'])} speakers per tenant copy: " + " · ".join(label(pp) for pp in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"{d['saas']} assistant · {d['account']} ({ARM_LABEL[ARM_OF[c]][3:]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per tenant project — what the meeting assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["projects"][c] = p["id"]
    for k in actor_keys(d):
        a = find_actor(cli, k)
        if a:
            world["actors"][k] = a["id"]
    world["boundaries"] = {b["custom_id"][len(PREFIX) + 1:]: b["id"] for b in demo_boundaries(cli)}
    return world


# --------------------------------------------------------------------------- step 3: the meetings

def show_meetings(d: dict) -> None:
    for m in d["meetings"]:
        emit("text", f"\n    {at(m):%a %d %b %Y}  “{m['name']}”")
        for x in d["lines"]:
            if x["meeting"] == m["id"]:
                tag = "reading" if x["kind"] == "reading" else "action "
                emit("text", wrap(f"{tag}  {person(d, x['speaker'])['display']}: {x['text']}", "        ", 112))
    emit("meetings", "", meetings=d["meetings"],
         lines=[dict(id=x["id"], meeting=x["meeting"], kind=x["kind"], what=x["what"], text=x["text"],
                     speaker=person(d, x["speaker"])["display"], value=x.get("value")) for x in d["lines"]])


def ingest_copy(cli: CLI, world: dict, copy: str, echo: bool, t0: float) -> list[str]:
    """One tenant copy: the three meetings, each a GROUP conversation, every line as its speaker a minute apart."""
    d = load_data()
    actors = {p["key"]: world["actors"][f"{copy}-{p['key']}"] for p in d["people"]}
    convs = []
    for n, m in enumerate(d["meetings"]):
        conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", ",".join(actors.values()),
                       "--kind", "GROUP", "--name", f"{d['org']} · {m['name']}", "--custom-id", cid("conv", copy, m["id"]),
                       scoped=True, echo=echo and n == 0)
        mine = [x for x in d["lines"] if x["meeting"] == m["id"]]
        for i, x in enumerate(mine):
            append(cli, conv["id"], actors[x["speaker"]], f"m{i:02d}", utc(at(m) + timedelta(minutes=i)),
                   f"{label(person(d, x['speaker']))}: {x['text']}", echo=echo and n == 0 and i == 0)
        convs.append((m, conv["id"]))
    late = []
    for n, (m, conv) in enumerate(convs):
        if not cooked(cli, conv, echo=echo and n == 0, limit=420):
            late.append(f"{copy}/{m['id']}")
        emit("progress", f"    {ARM_OF[copy]} {copy}: {m['name']} extracted · {int(time.time() - t0)} s", copy=copy, meeting=m["id"])
    return late


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_meetings(d)
    emit("text", f"\n    The same three conversations go into each tenant copy (each with its own two speakers); "
                 f"the three copies run side by side.")
    t0 = time.time()
    with ThreadPoolExecutor(3) as pool:
        late = sum(pool.map(lambda c: ingest_copy(cli, world, c, c == COPIES[0], t0), COPIES), [])
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed
    note(f"all three copies extracted in {int(time.time() - t0)} s")


# --------------------------------------------------------------------------- step 4: what each tenant remembers

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per tenant copy: the project's facts, which lines they carry, and lines that landed only on a speaker
    (MemoryRouter reads the project through the boundary, not the speakers)."""
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        on_actors = []
        for p in d["people"]:
            a = world["actors"].get(f"{c}-{p['key']}")
            if a:
                on_actors += [f.get("fact") or "" for f in actor_facts(cli, a)]
        lines = {x["id"]: dict(project=[f for f in facts if holds(x, f)], actor=[f for f in on_actors if holds(x, f)])
                 for x in d["lines"]}
        meetings = {}
        for m in d["meetings"]:
            mine = [x for x in d["lines"] if x["meeting"] == m["id"]]
            meetings[m["id"]] = dict(readings=sum(1 for x in mine if x["kind"] == "reading" and lines[x["id"]]["project"]),
                                     of=sum(1 for x in mine if x["kind"] == "reading"),
                                     action=any(lines[x["id"]]["project"] for x in mine if x["kind"] == "action"),
                                     empty=not any(lines[x["id"]]["project"] for x in mine))
        out[c] = dict(facts=len(facts), all=facts, actor_facts=on_actors, lines=lines, meetings=meetings,
                      readings=sum(1 for x in readings(d) if lines[x["id"]]["project"]),
                      actions=sum(1 for x in d["lines"] if x["kind"] == "action" and lines[x["id"]]["project"]),
                      empty_meetings=sum(1 for v in meetings.values() if v["empty"]),
                      only_on_actor=sum(1 for x in d["lines"] if lines[x["id"]]["actor"] and not lines[x["id"]]["project"]))
    return out


def show_census(cen: dict) -> None:
    d = load_data()
    emit("text", f"\n    {'':<24}" + "".join(f"{ARM_LABEL[a]:<26}" for a in ARMS))
    for m in d["meetings"]:
        cells = []
        for c in COPIES:
            v = cen[c]["meetings"][m["id"]]
            cells.append(f"{'⌀' if v['empty'] else ' '} readings {v['readings']}/{v['of']} · action {'✓' if v['action'] else '✗'}")
        emit("text", f"    {m['name']:<24}" + "".join(f"{x:<26}" for x in cells))
    emit("text", "")
    for x in d["lines"]:
        marks = "".join(f"{'✓' if cen[c]['lines'][x['id']]['project'] else ('·' if cen[c]['lines'][x['id']]['actor'] else '✗'):<26}"
                        for c in COPIES)
        emit("text", f"    {x['what'][:22]:<24}" + marks)
    emit("text", "")
    for c in COPIES:
        x = cen[c]
        emit("text", f"    {ARM_LABEL[ARM_OF[c]]:<24}{x['facts']:>3} facts in the project · readings {x['readings']}/{len(readings(d))} · "
                     f"action items {x['actions']}/3 · meetings with nothing kept {x['empty_meetings']}/3"
                     + (f" · {x['only_on_actor']} line(s) only on a speaker" if x["only_on_actor"] else ""))
    emit("text", "\n    ✓ in the tenant's project · ✗ not in memory · · only on a speaker (MemoryRouter does not read it) · "
                 "⌀ nothing from this meeting was kept")
    c = "instructed"
    ex = next((cen[c]["lines"][x["id"]]["project"][0] for x in readings(d) if cen[c]["lines"][x["id"]]["project"]), None)
    if ex:
        emit("text", wrap(f"C, a reading as kept: “{ex}”", "    ", 112))
    emit("census", "", **cen)


# --------------------------------------------------------------------------- step 5: ask the meeting assistant

def graded(x: dict, cen: dict | None, reply: str, arm: str) -> dict:
    held = bool(cen and cen[ARM_COPY[arm]]["lines"][x["id"]]["project"])
    return dict(grade=grade(x, reply), held=held)


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, cen: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = d["system"]
    qs = readings(d)
    jobs = [(x, i) for x in qs for i in range(RUNS)]
    for n, x in enumerate(qs):
        baseline(cli, x["ask"], system, echo=echo_first and n == 0)

    def one(job):
        x, i = job
        r = router(cli, x["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=x["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        empty = r["prompt_tokens"] <= BASE[(x["ask"], system)]
        return dict(item=x["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    **graded(x, cen, r["reply"], arm))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through each tenant's boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for x in qs:
        mine = [r for r in rows if r["item"] == x["id"]]
        marks = " ".join(mark(r) for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {x['what'][:30]:<31} {marks:<6} " + (first[:78] + "…" if len(first) > 79 else first))
        emit("ask_row", "", arm=arm, item=x["id"], rows=mine, value=x["value"])
    return rows


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    return dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
                empty=sum(1 for r in rows if r.get("grade") and r.get("empty")),
                ok=sum(1 for r in live if r["grade"] == "ok"), unknown=sum(1 for r in live if r["grade"] == "unknown"),
                made=sum(1 for r in live if r["grade"] == "made"), other=sum(1 for r in live if r["grade"] == "other"),
                ok_unheld=sum(1 for r in live if r["grade"] == "ok" and not r.get("held")))


EXAMPLE = "What recall at 10 did the new reranker get?"


def show_example(rows: list[dict]) -> None:
    """One question, the three tenants' replies side by side. Prefers a question that C's memory holds and A's or B's
    does not — the case the demo is about — and says so when there is none."""
    d = load_data()
    qs = readings(d)
    pick = None
    for x in [next(x for x in qs if x["ask"] == EXAMPLE)] + qs:
        got = {a: next((r for r in rows if r["arm"] == a and r["item"] == x["id"] and r.get("reply")), None) for a in ARMS}
        if all(got.values()) and got["C"].get("held") and not (got["A"].get("held") and got["B"].get("held")):
            pick = (x, got)
            break
    if not pick:
        note("no question this run where C's memory holds the reading and A's or B's does not")
        return
    x, got = pick
    emit("text", f"\n    “{x['ask']}”  (said at the {meeting_of(d, x['meeting'])['name']}: {x['value']})")
    for a in ARMS:
        r = got[a]
        emit("text", wrap(r["reply"], f"    {ARM_LABEL[a][:1]} {mark(r)} | ", 104))
    emit("example", "", ask=x["ask"], value=x["value"], rows={a: got[a] for a in ARMS})


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per reading, right / answered with something attached ({RUNS} answers per tenant; ∅ = MemoryRouter attached nothing):")
    emit("text", f"      {'':<32} " + "".join(f"{ARM_LABEL[a]:<24}" for a in ARMS))
    for x in readings(d):
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == x["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine), held=bool(cen[ARM_COPY[a]]["lines"][x["id"]]["project"]))
        out["by_item"][x["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']} {c['marks']}" + ("" if c["held"] else "  (not kept)")  # noqa: E731
        emit("text", f"      {x['what'][:31]:<32} " + "".join(f"{cell(cells[a]):<24}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    emit("text", f"    {'':<26}{'readings kept':>15}{'right':>10}{'Unknown':>9}{'invented':>10}{'other':>7}{'∅':>5}")
    for a in ARMS:
        t, c = out["answers"][a], cen[ARM_COPY[a]]
        emit("text", f"    {ARM_LABEL[a]:<26}{c['readings']:>12}/{len(readings(d)):<2}{t['ok']:>7}/{t['calls']:<3}{t['unknown']:>8}"
                     f"{t['made']:>10}{t['other']:>7}{t['empty']:>5}")
    emit("text", "\n    ✓ the reading · ? Unknown · ✎ a number memory does not hold · – no number · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_grader(d)
    BASE.clear()
    banner(2, TOTAL, f"Provision — {d['org']}'s tenant project, three ways")
    world = setup(cli)

    banner(3, TOTAL, "Three eval syncs — the same conversations into each tenant")
    ingest(cli, world)

    banner(4, TOTAL, "What each tenant remembers")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(5, TOTAL, "Ask the meeting assistant — every reading, through each tenant's memory")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, cen, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(6, TOTAL, "The score — what was kept, what the assistant could answer")
    sc = score(cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Per meeting and line: what each tenant's project kept."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", census=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each tenant; graded when it is one of the twelve readings."""
    d = load_data()
    world = load_world(cli)
    x = next((x for x in readings(d) if x["ask"].lower() == question.strip().lower()), None)
    cen = census(cli, world, echo=False) if x else None
    rows = []
    system = d["system"]
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = graded(x, cen, r["reply"], a) if x else {}
        head = "∅ nothing attached" if empty else (f"{MARK[g['grade']]} {GRADE_NAME[g['grade']]}" if g else "")
        if g:
            head += "   (kept in this tenant's memory)" if g["held"] else "   (not kept in this tenant's memory)"
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, **g))
    if x:
        emit("text", f"\n  said at the {meeting_of(d, x['meeting'])['name']}: {x['value']}")
    emit("answered", "", question=question, item=x and x["id"], rows=rows, value=x and x["value"])
    return rows


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
             if (c.get("custom_id") or "").startswith(PREFIX + "-")]
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    nfacts = 0
    found = [(k, find_actor(cli, k, echo=not quiet and k == actor_keys(d)[0])) for k in actor_keys(d)]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    nproj = 0
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            continue
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet and nproj == 0)
        nproj += 1
    if not quiet:
        n_act = sum(1 for _, a in found if a)
        note(f"deleted {len(bs)} boundaries, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts first: a "
             f"deleted actor's facts could never be removed) and {nproj} projects (with C's fact instruction)"
             if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = per meeting and line: what each tenant kept; "
                         "ask \"<question>\" = one question through A, B and C; cleanup = delete everything")
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
    if args.command == "memory":
        banner(2, 2, "What each tenant remembers")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the meeting assistant, through A, B and C")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask \"{EXAMPLE}\"`; "
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

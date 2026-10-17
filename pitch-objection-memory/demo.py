#!/usr/bin/env python3
"""pitch-objection-memory — one objection, captured in three tools, reads as a pattern.

Fernhallow (a fictional marketing agency) keeps its new business memory in MemoryLake and asks its pitch assistant,
through MemoryRouter, which objections keep coming up. Six SaaS pitches this year: three prospects pushed back on the
pricing model; one — Quorvia — raised a security objection (its SOC 2 vendor review). That one objection went into
memory three times, the way agency tools capture things: the CRM debrief says "Quorvia", the strategist's meeting
notes say "Project Lantern" (the pitch's code name), the Slack recap says "the payments prospect".

Each capture is extracted on its own, so memory holds three facts with three different subjects for one objection,
and the assistant counts subjects as prospects: "both the payments prospect and Quorvia asked for a SOC 2 review";
SOC 2 shows up in the list of objections that came up with more than one prospect. Three memories, the same five
questions: A every capture as it arrived · B the same pitches captured once each (the CRM debriefs only) · C one
record per pitch, written by the app after resolving every capture to its CRM opportunity — plus the same questions
with no memory at all, the baseline for what the model says on its own.

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
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pom"  # memorylake-usecases / pitch-objection-memory


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


# --------------------------------------------------------------------------- settings

MODEL = "gpt-4o-mini"
TEMPERATURE = "0.7"   # an assistant's usual setting: the same question gets a slightly different reply each time
RUNS = 4              # answers per question and arm
MAX_TOKENS = 110
COPIES = ("captured", "once", "records")   # the three projects
COPY_NAME = {"captured": "every capture as it arrived",
             "once": "each pitch captured once",
             "records": "one record per pitch, written by the app"}
ARMS = ("0", "A", "B", "C")
ARM_LABEL = {"0": "0  no memory", "A": "A  every capture as it arrived", "B": "B  each pitch captured once",
             "C": "C  one record per pitch"}
ARM_COPY = {"A": "captured", "B": "once", "C": "records"}
MARK = {"ok": "✓", "dup": "2×", "wrong": "✗", "unknown": "?"}
GRADE_NAME = {"ok": "right", "dup": "one objection counted as more than one prospect", "wrong": "wrong",
              "unknown": "says it does not know"}

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


# --------------------------------------------------------------------------- step 3: writing a capture

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, text: str, echo: bool) -> str | None:
    for attempt in range(5):
        try:
            res = cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                          "--text", text, scoped=True, echo=echo and attempt == 0)
            return (res or {}).get("id") if isinstance(res, dict) else None
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return None
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))






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


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "pitches.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [p["key"] for p in d["people"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=indent)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def pitch(d: dict, key: str) -> dict:
    return next(p for p in d["pitches"] if p["id"] == key)


def captures(d: dict, copy: str) -> list[dict]:
    """A gets every capture; B only the CRM debriefs — one per pitch, the same text."""
    return [c for c in d["captures"] if copy == "captured" or not c.get("repeat")]


def message_text(d: dict, c: dict) -> str:
    p = person(d, c["by"])
    return f"{p['display']} ({p['role']}): {c['text']}"


def questions(d: dict) -> list[dict]:
    return d["questions"]


# --------------------------------------------------------------------------- grading
# Every rule here has offline cases in selftest(); run `python3 demo.py selftest` after changing one.

SEC = r"\b(?:SOC ?2|security|vendor review)\b"
PRICE = r"\b(?:pric\w*|retainer|fees?|cost|expensive|per-campaign|performance-based)\b"
TIME = r"\b(?:timeline|launch|too slow|6 weeks|six weeks|12-week|live in)\b"
ALIAS = r"\b(?:payments prospect|payments platform prospect|Project Lantern|Lantern)\b"
UNKNOWN = (r"\b(?:don't have|do not have|not have (?:the |any |specific )?(?:exact |specific )?(?:number|data|information)|"
           r"no record|unable to|cannot (?:determine|confirm|say)|can't (?:determine|say)|not specified|not available|"
           r"additional data|need to check|no information)\b")
ONLY = r"\b(?:only|just|single|solely|one prospect|one-off|once)\b"
SAME = r"\b(?:same prospect|same pitch|same client|also (?:called|known)|referred to as|captured as|code ?name|i\.e\.|aka)\b"
NUM = {"no": 0, "zero": 0, "one": 1, "a single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
       "both": 2}
COUNT_RE = re.compile(r"\b(\d+|no|zero|one|two|three|four|five|six|seven|both)\b(?:\s+(?:different|distinct|other|SaaS|unique))*"
                      r"\s+(?:prospects?|companies|clients|of them|SaaS)", re.I)
OTHERS = ("Brisklane", "Tamberly", "Velloway", "Holbrine", "Mossquill")


def clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+|\b(?:additionally|also|as well as|furthermore|another)\b|,\s*and\s+|\s+and\s+(?=the\s)",
                     text, flags=re.I)
    return [p for p in parts if p and p.strip()]


def answer_body(reply: str) -> str:
    s = " ".join((reply or "").split())
    return re.sub(r"^\**answer:?\**\s*", "", s, flags=re.I)


def count_of(text: str) -> int | None:
    m = COUNT_RE.search(text)
    if m:
        w = m.group(1).lower()
        return int(w) if w.isdigit() else NUM.get(w)
    if re.search(r"\bonly\b[^.]{0,40}\bQuorvia\b|\bQuorvia\b[^.]{0,30}\bonly\b|\bthe only\b", text, re.I):
        return 1
    return None


def grade(q: dict, reply: str) -> str:
    t = answer_body(reply)
    unknown = bool(re.search(UNKNOWN, t, re.I))
    k = q["kind"]
    if k == "recurring":
        # A clause that says "only", "not", or pins SOC 2 on Quorvia alone does not list it as recurring.
        listed = [c for c in clauses(t) if not re.search(ONLY + r"|\bnot\b|\bnever\b", c, re.I)
                  and not (re.search(r"\bQuorvia\b", c) and not re.search(ALIAS, t, re.I))]
        sec = any(re.search(SEC, c, re.I) for c in listed)
        tim = any(re.search(TIME, c, re.I) for c in listed)
        price = re.search(PRICE, t, re.I)
        if sec:
            return "dup"
        if price and not tim:
            return "ok"
        return "unknown" if unknown and not price else "wrong"
    if k == "list_soc2":
        same = re.search(SAME, t, re.I)
        # The nickname next to the name is a second prospect; the nickname alone is just not an answer (✗).
        alias = re.search(ALIAS, t, re.I) and re.search(r"\bQuorvia\b", t) and not same
        many = re.search(r"(?<!not )\b(?:both|two|multiple|several|more than one|each of them)\b", t, re.I) and not \
            re.search(r"\bonly\b", t, re.I)
        if alias or many:
            return "dup"
        if any(re.search(rf"\b{o}\b", t) for o in OTHERS):
            return "wrong"
        if re.search(r"\bQuorvia\b", t):
            return "ok"
        return "unknown" if unknown else "wrong"
    if k == "count":
        # "one prospect (the payments prospect) and another (Quorvia)": the alias and the name both counted.
        if q["topic"] == "security" and re.search(ALIAS, t, re.I) and re.search(r"\bQuorvia\b", t) \
                and not re.search(SAME, t, re.I):
            return "dup"
        n = count_of(t)
        if n is None:
            if q["topic"] == "security" and re.search(ALIAS, t, re.I) and re.search(r"\bQuorvia\b", t):
                return "dup"
            return "unknown" if unknown or not re.search(r"\d", t) else "wrong"
        if n == q["n"]:
            return "ok"
        # More prospects than there are is 2× only for the objection that was captured three times.
        if n > q["n"]:
            return "dup" if q["topic"] == "security" else "wrong"
        return "unknown" if unknown and n == 0 else "wrong"
    if k == "name":
        if re.search(rf"\b{q['name']}\b", t):
            return "ok"
        return "unknown" if unknown else "wrong"
    raise ValueError(k)


CASES = [
    ("Q1", "Answer: Pricing model objections came up with Brisklane, Tamberly and Holbrine.", "ok"),
    ("Q1", "Answer: Pricing (retainer vs fixed fee) recurred; the SOC 2 review came up only with Quorvia.", "ok"),
    ("Q1", "Answer: Concerns about pricing models, and the need for SOC 2 compliance before moving forward.", "dup"),
    ("Q1", "Answer: Pricing, as well as security issues related to SOC 2 vendor reviews.", "dup"),
    ("Q1", "Answer: The cost of retainers (Tamberly) and the need for a quicker launch timeline (Velloway).", "wrong"),
    ("Q1", "Answer: I don't have information on objections from more than one prospect.", "unknown"),
    ("Q2", "Answer: Only Quorvia asked us to pass a SOC 2 review.", "ok"),
    ("Q2", "Answer: Quorvia (the payments platform).", "ok"),
    ("Q2", "Answer: Both the payments prospect and Quorvia have requested a SOC 2 review.", "dup"),
    ("Q2", "Answer: Quorvia and Project Lantern.", "dup"),
    ("Q2", "Answer: Quorvia; Project Lantern was the code name for the same pitch.", "ok"),
    ("Q2", "Answer: Quorvia and Brisklane.", "wrong"),
    ("Q2", "Answer: The payments prospect asked us to pass a SOC 2 review.", "wrong"),
    ("Q2", "Answer: I don't have a record of which prospects asked for a SOC 2 review.", "unknown"),
    ("Q3", "Answer: This year, only one prospect, Quorvia, raised a security or SOC 2 concern.", "ok"),
    ("Q3", "Answer: Quorvia is the only prospect noted to have raised a security concern.", "ok"),
    ("Q3", "Answer: This year, there are two different prospects that raised a security or SOC 2 concern.", "dup"),
    ("Q3", "Answer: At least two different prospects, including the payments prospect and Quorvia.", "dup"),
    ("Q3", "Answer: I don't have the exact number of different prospects that raised a security concern.", "unknown"),
    ("Q3", "Answer: This year, one prospect has raised a security concern (the payments prospect), and another has "
           "raised a SOC 2 concern (Quorvia).", "dup"),
    ("Q1", "Answer: The pricing model. Additionally, the security concern regarding a SOC 2 vendor review was raised by "
           "Quorvia.", "ok"),
    ("Q1", "Answer: Pricing, and the SOC 2 review that Quorvia and the payments prospect asked for.", "dup"),
    ("Q4", "Answer: Three different prospects pushed back on our pricing model: Brisklane, Tamberly and Holbrine.", "ok"),
    ("Q4", "Answer: This year, two different prospects pushed back on our pricing model: Brisklane and Tamberly.", "wrong"),
    ("Q4", "Answer: We had at least one prospect, Tamberly, push back on our pricing.", "wrong"),
    ("Q4", "Answer: 3 prospects.", "ok"),
    ("Q4", "Answer: This year, we had a total of 15 different prospects who expressed concerns about pricing.", "wrong"),
    ("Q5", "Answer: Velloway said our 12-week launch plan was too slow.", "ok"),
    ("Q5", "Answer: Quorvia.", "wrong"),
]


def arm_grade(arm: str, q: dict, reply: str) -> str:
    """With no memory there are no repeats to count: an inflated number there is invented, so it is ✗, not 2×."""
    g = grade(q, reply)
    return "wrong" if arm == "0" and g == "dup" else g


def selftest() -> int:
    qs = {q["id"]: q for q in questions(load_data())}
    bad = [(qid, s, want, grade(qs[qid], s)) for qid, s, want in CASES if grade(qs[qid], s) != want]
    for qid, s, want, got in bad:
        print(f"FAIL {qid} want {want} got {got}: {s}")
    print(f"grader cases: {len(CASES) - len(bad)}/{len(CASES)} pass")
    return 1 if bad else 0


# --------------------------------------------------------------------------- census: what memory holds per pitch

def mentions(d: dict, p: dict, text: str) -> bool:
    names = [p["name"]] + [c["called"] for c in d["captures"] if c["pitch"] == p["id"]]
    return any(re.search(rf"\b{re.escape(n)}\b", text, re.I | re.A) for n in names)


def subject_of(d: dict, text: str) -> str:
    """Which name a fact about the SOC 2 objection uses for the prospect."""
    for c in d["captures"]:
        if c["pitch"] == "quorvia" and c["called"] != "Quorvia" and re.search(rf"\b{re.escape(c['called'])}\b", text, re.I):
            return c["called"]
    if re.search(r"\bQuorvia\b", text):
        return "Quorvia"
    return "no name"


def census_copy(d: dict, facts: list[str]) -> dict:
    out: dict = {"pitches": {}, "facts": len(facts)}
    kw = {"pricing": PRICE, "security": SEC, "timeline": TIME, "none": r"\b(?:no objections?|liked|won)\b"}
    for p in d["pitches"]:
        mine = [f for f in facts if mentions(d, p, f)]
        kept = [f for f in mine if re.search(kw[p["objection"]], f, re.I)]
        out["pitches"][p["id"]] = dict(facts=len(mine), kept=bool(kept), shown=(kept or mine or [""])[0])
    sec = [f for f in facts if re.search(SEC, f, re.I)]
    out["security_facts"] = [dict(text=f, subject=subject_of(d, f)) for f in sec]
    out["security_subjects"] = sorted({s["subject"] for s in out["security_facts"]})
    out["kept"] = sum(1 for v in out["pitches"].values() if v["kept"])
    return out


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three, and no arm letter in it: a project's name is context for extraction.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} new business",
                                       "--custom-id", cid(c), "--description", f"{d['org']} new business pitches. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name: A gets every capture, B one capture per pitch, C the app's pitch records")
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}; writes in {p['tool']}. Demo data.",
                                               echo=n == 0)
    note(f"{len(d['people'])} people who capture pitches: " + ", ".join(f"{p['display']} ({p['tool']})" for p in d["people"]))
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Pitch assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the pitch assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def demo_convs(cli: CLI) -> dict:
    return {c["custom_id"][len(cid("conv")) + 1:]: c["id"] for c in paged(cli, ["conv", "list", "--page-size", "100"])
            if (c.get("custom_id") or "").startswith(cid("conv") + "-")}


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
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
    world["convs"] = demo_convs(cli)
    return world


# --------------------------------------------------------------------------- step 3: the captures

def show_captures(d: dict) -> None:
    emit("text", f"\n    {'pitch':<11}{'tool':<15}{'by':<12}{'calls the prospect':<23}{'date':<12}objection")
    for c in d["captures"]:
        p = pitch(d, c["pitch"])
        tag = "   ← same pitch, again" if c.get("repeat") else ""
        emit("text", f"    {p['name']:<11}{c['tool']:<15}{person(d, c['by'])['display']:<12}{c['called']:<23}"
                     f"{c['ts'][:10]:<12}{p['objection']}{tag}")
    emit("captures", "", rows=[dict(c, pitch_name=pitch(d, c["pitch"])["name"], person=person(d, c["by"])["display"],
                                    objection=pitch(d, c["pitch"])["objection"]) for c in d["captures"]])


def wait_read(cli: CLI, conv: str, msg: str | None, echo: bool, limit: int = 420) -> bool:
    """A message is read when it shows up in its extraction batch and the conversation reports cook_finished."""
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            batch = cli.run("conv", "consumed-messages", conv, "--message", msg, scoped=True, echo=first) if msg else [1]
            first = False
            items = batch.get("items") if isinstance(batch, dict) else batch
            if items:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=False) or {}
                if st.get("cook_finished"):
                    return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(4)
    return False


def ingest_one(cli: CLI, world: dict, copy: str, c: dict, echo: bool, t0: float) -> tuple[str, bool]:
    d = load_data()
    actor = world["actors"][c["by"]]
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", actor, "--kind", "GROUP",
                   "--name", f"{c['tool']}: {c['called']}", "--custom-id", cid("conv", copy, c["id"]), scoped=True, echo=echo)
    mid = append(cli, conv["id"], actor, cid("msg", copy, c["id"]), c["ts"], message_text(d, c), echo=echo)
    ok = wait_read(cli, conv["id"], mid, echo=echo)
    emit("progress", f"    {copy:<9} {c['tool']:<14} {c['called']:<22} read · {int(time.time() - t0)} s", copy=copy, capture=c["id"])
    return conv["id"], ok


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_captures(d)
    emit("text", "\n    Each capture is one message in its own GROUP conversation, written by the person who wrote it, stamped "
                 "with the day it was written.\n    A gets all eight; B gets the six CRM debriefs — the same pitches, the "
                 "same text, without the two repeats.")
    emit("text", wrap(message_text(d, next(c for c in d["captures"] if c["id"] == "quorvia-slack")), "    ", 112))
    t0 = time.time()
    jobs = [(copy, c) for copy in ("captured", "once") for c in captures(d, copy)]
    with ThreadPoolExecutor(14) as pool:
        res = list(pool.map(lambda j: ingest_one(cli, world, j[0], j[1], j is jobs[0], t0), jobs))
    world["convs"] = {f"{j[0]}-{j[1]['id']}": r[0] for j, r in zip(jobs, res)}
    late = [f"{j[0]}/{j[1]['id']}" for j, r in zip(jobs, res) if not r[1]]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — read as they stand")
    note(f"{len(jobs)} conversations, {len(jobs)} messages, all read in {int(time.time() - t0)} s")
    time.sleep(8)


# --------------------------------------------------------------------------- step 4: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(("captured", "once")):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        out[copy] = dict(all=facts, **census_copy(d, facts))
    return out


def show_census(d: dict, cs: dict) -> None:
    emit("text", f"\n    {'pitch':<11}{'objection':<11}" + "".join(f"{ARM_LABEL[a][:31]:<33}" for a in ("A", "B")))
    for p in d["pitches"]:
        cells = []
        for copy in ("captured", "once"):
            v = cs[copy]["pitches"][p["id"]]
            cells.append(("kept" if v["kept"] else ("named, objection lost" if v["facts"] else "NOT IN MEMORY"))
                         + f" ({v['facts']} fact{'s' if v['facts'] != 1 else ''})")
        emit("text", f"    {p['name']:<11}{p['objection']:<11}" + "".join(f"{c:<33}" for c in cells))
    for copy, a in (("captured", "A"), ("once", "B")):
        c = cs[copy]
        emit("text", f"\n    {a}: {len(c['security_facts'])} fact(s) carry the SOC 2 objection; the prospect in them is called: "
                     + (", ".join(c["security_subjects"]) or "—"))
        for s in c["security_facts"]:
            emit("text", wrap(f"[{s['subject']}] {s['text']}", "      · ", 118))
    for copy, a in (("captured", "A"), ("once", "B")):
        lost = [pitch(d, k)["name"] for k, v in cs[copy]["pitches"].items() if not v["kept"]]
        emit("text", f"    {a}: {cs[copy]['facts']} facts; the objection of {cs[copy]['kept']}/6 pitches is in memory"
                     + (f" (lost: {', '.join(lost)})" if lost else ""))
    emit("census", "", **{k: {kk: vv for kk, vv in v.items()} for k, v in cs.items()})


# --------------------------------------------------------------------------- step 5: C — one record per pitch

def resolve(d: dict) -> list[dict]:
    """The app's join: each capture carries a link (the CRM opportunity, the calendar event attached to it, the deal
    card the Slack thread hangs off). Every link names an opportunity, so every capture lands on exactly one pitch."""
    by_opp = {p["opp"]: p for p in d["pitches"]}
    out = []
    for c in d["captures"]:
        m = re.search(r"OPP-\d+", c["link"])
        if not m or m.group(0) not in by_opp:
            die(f"capture {c['id']} has no opportunity in its link ({c['link']})")
        out.append(dict(capture=c["id"], tool=c["tool"], called=c["called"], link=c["link"], opp=m.group(0),
                        pitch=by_opp[m.group(0)]["id"], name=by_opp[m.group(0)]["name"]))
    return out


def record_text(d: dict, p: dict, srcs: list[dict]) -> str:
    seen = ", ".join(f"{s['tool']} ({s['called']})" if s["called"] != p["name"] else s["tool"] for s in srcs)
    once = f" Captured {len(srcs)} times ({seen}): one pitch, one prospect." if len(srcs) > 1 else f" Source: {seen}."
    obj = "none" if p["objection"] == "none" else f"{d['objections'][p['objection']]} - {p['detail']}"
    return (f"Pitch record (CRM opportunity {p['opp']}): {p['name']}, {p['vertical']}, pitched {p['date']}. "
            f"Objection: {obj}.{once} Outcome: {p['outcome']}.")


def write_records(cli: CLI, world: dict) -> dict:
    d = load_data()
    res = resolve(d)
    emit("text", "\n    The app resolves every capture to a CRM opportunity by its link, not by the name it uses:")
    for r in res:
        emit("text", f"      {r['tool']:<14}{('“' + r['called'] + '”'):<24}{r['link'][:58]:<60}→ {r['opp']} {r['name']}")
    recs = []
    for p in d["pitches"]:
        recs.append(record_text(d, p, [r for r in res if r["pitch"] == p["id"]]))
    cli.run("fact", "add", *recs, "--project", world["projects"]["records"], scoped=True, echo=True)
    note(f"C: {len(res)} captures → {len(recs)} pitch records, one per opportunity (`fact add`, verbatim)")
    for t in recs[1:2]:
        emit("text", wrap(t, "      · ", 118))
    emit("records", "", resolved=res, records=recs)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`
    return dict(resolved=res, records=recs)


# --------------------------------------------------------------------------- step 6: ask

def system_prompt(d: dict) -> str:
    return d["system"].format(org=d["org"], today=today(d).strftime("%A %Y-%m-%d"))


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    qs = questions(d)
    jobs = [(q, i) for q in qs for i in range(RUNS)]
    for n, q in enumerate(qs):
        baseline(cli, q["ask"], system, echo=echo_first and n == 0)

    def one(job):
        q, i = job
        bd = world["boundaries"][ARM_COPY[arm]] if arm != "0" else None
        r = router(cli, q["ask"], system, bd, echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        empty = arm != "0" and r["prompt_tokens"] <= BASE[(q["ask"], system)]
        return dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    grade=arm_grade(arm, q, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers per memory; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        pick = next((r for r in mine if r.get("grade") == "dup"), None) or next((r for r in mine if r.get("reply")), None)
        first = answer_body(pick["reply"]) if pick else ""
        emit("text", f"    {q['what']:<38} {marks:<12} " + (first[:72] + "…" if len(first) > 73 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine, truth=q["truth"])
    return rows


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    return t


def show_example(rows: list[dict]) -> None:
    """Q2 through every memory: the reply that counts a code name or a nickname as another prospect, set beside B and C."""
    d = load_data()
    q = next(q for q in questions(d) if q["kind"] == "list_soc2")
    if not any(r["arm"] == "A" and r["item"] == q["id"] and r.get("grade") == "dup" for r in rows):
        q = next((x for x in questions(d) for r in rows if r["arm"] == "A" and r["item"] == x["id"] and r.get("grade") == "dup"), None)
        if not q:
            note("no answer through A counted the repeats as more prospects this run — nothing to set side by side")
            return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "dup")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{q['ask']}”  (truth: {q['truth']})")
            emit("text", wrap(r["reply"], f"    {mark(r)} | ", 104))
            emit("example", "", arm=a, row=r, ask=q["ask"], truth=q["truth"])


def score(rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per memory; ∅ = MemoryRouter attached nothing, not graded):")
    emit("text", f"      {'':<50}" + "  ".join(f"{a:<14}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), dup=sum(1 for r in live if r["grade"] == "dup"),
                            n=len(live), empty=len(mine) - len(live), marks="".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']} {c['marks']}"  # noqa: E731
        emit("text", f"      {(q['id'] + ' ' + q['what'] + ' = ' + q['truth'])[:49]:<50}" + "  ".join(f"{cell(cells[a]):<14}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<34}{'right':>9}{'2× counted twice':>18}{'✗':>4}{'?':>4}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<34}{x['ok']:>5}/{x['n']:<3}{x['dup']:>17}{x['wrong']:>4}{x['unknown']:>4}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ right · 2× one objection counted as more than one prospect (or a one-off listed as recurring) · "
                 "✗ wrong · ? says it does not know · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s pitch memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, "Eight captures of six pitches — Quorvia's objection is in three tools")
    ingest(cli, world)

    banner(4, TOTAL, "What memory now holds about each pitch")
    cs = census(cli, world)
    show_census(d, cs)

    banner(5, TOTAL, "C — the app writes one record per pitch")
    rec = write_records(cli, world)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the pitch assistant — no memory and three memories, the same five questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == "A")
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cs, records=rec, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok, today=today(d).isoformat())  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Re-read A's and B's facts: which pitches kept their objection, and what the SOC 2 facts call the prospect."""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census=cs)
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once with no memory and once through each memory; graded when it is one of the five."""
    d = load_data()
    world = load_world(cli)
    q = next((q for q in questions(d) if q["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = system_prompt(d)
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        bd = world["boundaries"].get(ARM_COPY[a]) if a != "0" else None
        r = router(cli, question, system, bd, echo=n == 1)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = a != "0" and r["prompt_tokens"] <= base
        g = arm_grade(a, q, r["reply"]) if q else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, grade=g))
    if q:
        emit("text", f"\n  truth: {q['truth']}")
    emit("asked", "", question=question, item=q and q["id"], rows=rows, truth=q and q["truth"])
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
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda c: cli.run("conv", "delete", c["id"], scoped=True, echo=False), convs))
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
             f"deleted actor's facts could never be removed) and {nproj} projects" if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


EXAMPLE = "Which prospects asked us to pass a SOC 2 review? List each prospect once."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup", "selftest"],
                    help="run = full demo (default); memory = re-read what A's and B's facts hold per pitch; "
                         "ask \"<question>\" = one question with no memory and through A, B and C; cleanup = delete "
                         "everything; selftest = the grader's offline cases (no account needed)")
    ap.add_argument("args", nargs="*", help="for ask: the question")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "selftest":
        sys.exit(selftest())
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "memory":
        banner(2, 2, "What A's and B's facts hold about each pitch")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the pitch assistant: no memory, then A, B and C")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask '{EXAMPLE}'`; "
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

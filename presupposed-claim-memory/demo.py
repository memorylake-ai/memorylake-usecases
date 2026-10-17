#!/usr/bin/env python3
"""presupposed-claim-memory — "Now that Halden owns Lithra…": a research question that assumes a rumour, saved as asked.

Tarnwell Research (fictional): Mira Okafor covers the battery recycling market and does her searching in Perplexity.
Every research thread is saved to MemoryLake when it ends — question and answer, as they were. In the first week she
finds three things that are not established: an anonymous-source report that Halden Group is in talks to buy Lithra
Recycling, Ostrel's own claim of a 91 percent lithium recovery rate (the only independent test found 64), and
Pellmont's plan to grow its Ohio plant to 60,000 tonnes a year (it processes 18,000 today; the plan needs a grant).
Memory keeps all three as claims.

Over the next weeks her questions take them for granted — "Now that Halden Group owns Lithra Recycling, which plants
does the combined company run?" — and the answers deal only with the other half of the question. The question is
saved too, and what it assumes can come back from memory as a fact, sometimes in place of the right one ("Today
Pellmont's Ohio plant has a capacity of 60,000 tonnes per year (… previously … 18,000 …)").

Three memories of the same twelve threads, the same eight questions: A every thread as asked and answered · B the
answers only · C as A, plus the app's finding log (one fact per finding, with its status) — and the same questions
with no memory at all, the baseline for what the model says on its own.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 100 calls on gpt-4o-mini per run).
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pcl"  # memorylake-usecases / presupposed-claim-memory


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
                 "http 502", "http 503", "http 504", "http 429", "too many requests")

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
RUNS = 3              # answers per question and arm
MAX_TOKENS = 120
COPIES = ("asked", "answers", "logged")   # the three projects
COPY_NAME = {"asked": "every thread as asked and answered",
             "answers": "the answers only",
             "logged": "as asked and answered + the finding log"}
ARMS = ("0", "A", "B", "C")
ARM_LABEL = {"0": "0  no memory", "A": "A  threads as asked and answered", "B": "B  the answers only",
             "C": "C  as A + the finding log"}
ARM_COPY = {"A": "asked", "B": "answers", "C": "logged"}
MARK = {"ok": "✓", "taken": "⚑", "wrong": "✗", "unknown": "?"}
GRADE_NAME = {"ok": "right", "taken": "states the unproven claim as fact", "wrong": "wrong",
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


# --------------------------------------------------------------------------- writing a text

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

def load_data() -> dict:
    return json.loads((DATA / "research.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def people(d: dict) -> list[dict]:
    return [d["analyst"], d["assistant"]]


def actor_keys(d: dict) -> list[str]:
    # one analyst and one assistant per project: facts can land on the speakers, and the three memories share nothing
    return [f"{c}-{p['key']}" for c in COPIES for p in people(d)]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = indent.replace("·", " ").replace("⚑", " ") if ("·" in indent or "⚑" in indent) else indent
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=sub)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def claim(d: dict, key: str) -> dict:
    return next(c for c in d["claims"] if c["id"] == key)


def questions(d: dict) -> list[dict]:
    return d["questions"]


def sessions(d: dict, week1: bool) -> list[dict]:
    return [s for s in d["sessions"] if (s["week"] == 1) == week1]


# --------------------------------------------------------------------------- reading claims out of text
# Every rule here has offline cases in selftest(); run `python3 demo.py selftest` after changing one.

NUM = {
    "60k": r"(?<![\d.,])60[,. ]?000(?![\d])|(?<![\d.,$])60\s?k\b|(?<![\d.,])60 thousand",
    "18k": r"(?<![\d.,])18[,. ]?000(?![\d])|(?<![\d.,$])18\s?k\b|(?<![\d.,])18 thousand",
    "25k": r"(?<![\d.,])25[,. ]?000(?![\d])|(?<![\d.,$])25\s?k\b|(?<![\d.,])25 thousand",
    "91": r"(?<![\d.,])91(?:\.0)?\s?(?:%|percent\b|per cent\b)",
    "64": r"(?<![\d.,])64(?:\.0)?\s?(?:%|percent\b|per cent\b)",
    "410": r"(?<![\d.,])410\s?(?:million\b|m\b|mn\b)|(?<![\d.,])0\.41\s?billion",
    "passport": r"(?<![\d])18(?:th)?\s+(?:of\s+)?February,?\s+2027|February\s+18(?:th)?,?\s+2027|2027-02-18|\bFebruary\s+2027\b",
}
# The part of a fact (or an answer) after this is the old value the extraction kept for the record.
PREVIOUSLY = r"\bpreviously\b|\bformerly\b|\boriginally\b|\binitially\b|\bup from\b"
# Words that keep a figure a claim, a plan or a report rather than a fact.
HEDGE = (r"\b(?:plan|plans|planned|planning|expan\w*|propos\w*|grant|pending|depend\w*|would|could|aim\w*|target\w*|"
         r"intend\w*|future|if|once|eventual\w*|potential\w*|claim\w*|says?|said|report|reports|reported|according|deck|"
         r"self-reported|unverified|unconfirmed|alleged\w*|rumou?r\w*|talks|anonymous|purported\w*|asserts?|"
         r"not (?:been )?(?:independently )?(?:verified|confirmed|proven))\b")
NEG = r"\b(?:not|no|never|nothing|neither|nor)\b|n't\b"
ACQUIRED = (r"\b(?:acquired|acquisition of|bought|purchased|purchase of|owns|owned by|paid|took over|takeover of|"
            r"combined (?:[\w–-]+ ){0,4}company|merged)\b")
UNKNOWN = (r"\b(?:don't have|do not have|no record|not recorded|not (?:been )?(?:stated|mentioned|specified|provided|"
           r"available|given|known|disclosed)|unable to|cannot (?:determine|confirm|say)|can't (?:determine|say|confirm)|"
           r"no information|no (?:specific )?(?:details?|data)|unknown|not sure|uncertain)\b")


def has(key: str, text: str) -> bool:
    return bool(re.search(NUM[key], text or "", re.I))


def main_part(text: str) -> str:
    m = re.search(PREVIOUSLY, text or "", re.I)
    return text[:m.start()] if m else (text or "")


def answer_body(reply: str) -> str:
    s = " ".join((reply or "").split())
    return re.sub(r"^\**answer:?\**\s*", "", s, flags=re.I)


def clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+|,(?!\d)\s*(?:and\s+|but\s+|while\s+|whereas\s+)?|\s+(?:but|while|whereas|although|though)\s+|"
                     r"\s*\(|\)|\s+—\s+|\s+-\s+", text, flags=re.I)
    return [p for p in parts if p and p.strip()]


def stated(key: str, text: str) -> bool:
    """Does the text state the claim as an established fact, in some clause with no hedge on it?"""
    for c in clauses(main_part(text)):
        if re.search(PREVIOUSLY, c, re.I):
            continue
        if key == "pellmont" and has("60k", c) and not re.search(HEDGE, c, re.I):
            return True
        if key == "ostrel" and has("91", c) and not re.search(HEDGE, c, re.I):
            return True
        if key == "halden":
            # "Halden paid 410 million" is stated even when "reportedly" is on it: a payment is not a report of talks.
            if re.search(ACQUIRED, c, re.I) and not re.search(NEG + r"|\btalks\b|\bunconfirmed\b|\brumou?r\w*|\bif\b|\bwould\b|"
                                                              r"\bcould\b|\bpotential\w*|\bcannot\b|\bunable\b|\bwhether\b", c, re.I):
                return True
            if has("410", c) and not re.search(NEG + r"|\btalks\b|\bunconfirmed\b|\brumou?r\w*|\breport\b|\breports\b|"
                                                     r"\breported\b|\bif\b|\bwould\b|\bcould\b", c, re.I):
                return True
    return False


def starts(word: str, text: str) -> bool:
    return bool(re.match(rf"\W*{word}\b", text, re.I))


def grade(d: dict, q: dict, reply: str) -> str:
    """ok · taken (states the unproven claim as fact) · wrong · unknown."""
    t = answer_body(reply)
    unknown = bool(re.search(UNKNOWN, t, re.I))
    if q["id"] == "Q1":
        if stated("pellmont", t):
            return "taken"
        return "ok" if has("18k", t) else "unknown" if unknown else "wrong"
    if q["id"] == "Q2":
        if starts("yes", t):
            return "taken" if stated("pellmont", t) or not re.search(HEDGE, t, re.I) else "wrong"
        if stated("pellmont", t):
            return "taken"
        if starts("no", t) or has("18k", t) or re.search(r"\bsmaller\b|\bnot (?:bigger|larger)\b|\bLeipzig\b[^.]*\b(?:bigger|larger)\b|"
                                                         r"\bless than\b", t, re.I):
            return "ok"
        return "unknown" if unknown else "wrong"
    if q["id"] == "Q3":
        if has("64", t):
            return "ok"
        if stated("ostrel", t):
            return "taken"
        return "ok" if has("91", t) else "unknown" if unknown else "wrong"
    if q["id"] == "Q4":
        if starts("yes", t):
            return "taken" if stated("ostrel", t) or not re.search(HEDGE, t, re.I) else "wrong"
        if starts("no", t) or has("64", t) or re.search(
                r"\bnot (?:clear|established|possible|verified|independently|confirmed|known)\b|\bcannot\b|\bcan't\b|"
                r"\bunverified\b|\bunclear\b|\bclaim\w*\b|\bno definitive\b|\bwhether\b", t, re.I):
            return "ok"
        if re.search(r"\bOstrel\b[^.]*\b(?:more|higher|better|outperform\w*|exceed\w*)\b", t, re.I):
            return "taken"
        return "unknown" if unknown else "wrong"
    if q["id"] == "Q5":
        if starts("yes", t) or stated("halden", t):
            return "taken"
        if re.search(r"\bunconfirmed\b|\btalks\b|\breport\w*|\brumou?r\w*|\bnot (?:officially |yet )?(?:acquired|confirmed)\b|"
                     r"\bno (?:deal|acquisition|confirmation)\b", t, re.I):
            return "ok"
        if unknown:
            return "unknown"
        return "ok" if starts("no", t) or re.search(NEG, t, re.I) else "wrong"
    if q["id"] == "Q6":
        if stated("halden", t):
            return "taken"
        if re.search(r"\btalks\b|\bunconfirmed\b|\bnot (?:been )?(?:confirmed|completed|announced|finali[sz]ed)\b|"
                     r"\bno (?:deal|acquisition|confirmed|price|payment)\b|\bhas(?:n't| not)\b|\bdid(?:n't| not)\b|\bnothing\b",
                     t, re.I):
            return "ok"
        return "unknown" if unknown else "wrong"
    if q["id"] == "Q7":
        return "ok" if has("passport", t) else "unknown" if unknown else "wrong"
    if q["id"] == "Q8":
        return "ok" if has("25k", t) else "unknown" if unknown else "wrong"
    raise ValueError(q["id"])


CASES = [
    ("Q1", "Pellmont's Ohio plant currently processes 18,000 tonnes a year, with a planned capacity of 60,000 tonnes a year pending a state grant.", "ok"),
    ("Q1", "Pellmont's Ohio plant currently has a processing capacity of 60,000 tonnes per year.", "taken"),
    ("Q1", "Today Pellmont's Ohio plant has a capacity of 60,000 tonnes per year, previously 18,000 tonnes.", "taken"),
    ("Q1", "The Ohio plant processes 18,000 tonnes a year; Pellmont plans to expand it to 60,000 tonnes.", "ok"),
    ("Q1", "Pellmont's Ohio plant processes 60,000 tonnes a year, up from 18,000.", "taken"),
    ("Q1", "I don't have the current capacity of Pellmont's Ohio plant.", "unknown"),
    ("Q1", "Pellmont's Ohio plant has a capacity of 40,000 tonnes a year.", "wrong"),
    ("Q2", "Yes, Pellmont's Ohio plant, with a current capacity of 60,000 tonnes per year, is bigger than Brisa Cycle's Leipzig plant, which has a capacity of 25,000 tonnes per year.", "taken"),
    ("Q2", "No, Pellmont's Ohio plant processes 18,000 tonnes a year, smaller than Brisa Cycle's 25,000 tonnes.", "ok"),
    ("Q2", "Brisa Cycle's Leipzig plant is larger, at 25,000 tonnes a year versus 18,000 for Pellmont's Ohio plant.", "ok"),
    ("Q2", "Pellmont's Ohio plant at 60,000 tonnes a year is larger than Leipzig's 25,000.", "taken"),
    ("Q2", "Yes, if Pellmont's planned expansion to 60,000 tonnes goes ahead it would be bigger.", "wrong"),
    ("Q2", "Pellmont's Ohio plant currently processes 18,000 tonnes per year, but with the planned expansion of 60,000 tonnes per year, it will exceed Brisa Cycle's Leipzig plant capacity of 25,000 tonnes annually once the expansion is completed.", "ok"),
    ("Q2", "Yes, Pellmont's Ohio plant, with a current capacity of 18,000 tonnes per year and a planned expansion to 60,000 tonnes per year, will be larger than Brisa Cycle's Leipzig plant, which has a capacity of 25,000 tonnes per year.", "wrong"),
    ("Q3", "Ostrel's lithium recovery rate is 91 percent.", "taken"),
    ("Q3", "Ostrel claims a lithium recovery rate of 91 percent, but independent testing from 2024 reported only 64 percent.", "ok"),
    ("Q3", "Ostrel claims a 91 percent lithium recovery rate in its investor deck; it has not been independently verified.", "ok"),
    ("Q3", "Ostrel's lithium recovery rate is 91 percent, although the only independent test found 64 percent.", "ok"),
    ("Q3", "Ostrel has a 91% lithium recovery rate according to the company.", "ok"),
    ("Q3", "I don't have information on Ostrel's lithium recovery rate.", "unknown"),
    ("Q4", "Ostrel has a higher lithium recovery rate at 91 percent compared to Varra's 85 percent.", "taken"),
    ("Q4", "Yes, Ostrel recovers more lithium, at 91 percent compared to Varra's 85 percent.", "taken"),
    ("Q4", "Not necessarily: Ostrel claims 91 percent, but the only independent test found 64 percent, below Varra's reported 85 percent.", "ok"),
    ("Q4", "No, Varra's 85 percent is higher than Ostrel's independently measured 64 percent.", "ok"),
    ("Q4", "Ostrel's claimed 91 percent is higher than Varra's 85 percent, but it is unverified.", "ok"),
    ("Q4", "As of my last update in October 2023, there was no definitive comparison indicating whether Ostrel recovers more lithium than Varra.", "ok"),
    ("Q4", "I don't have the latest data beyond October 2023, but as of that time, Ostrel was reported to have a more efficient lithium recovery process compared to Varra.", "taken"),
    ("Q5", "As of now, Halden Group has not officially acquired Lithra Recycling; reports indicate they are in talks, but this information remains unconfirmed.", "ok"),
    ("Q5", "Yes, Halden Group acquired Lithra Recycling and now runs its plant in Ghent.", "taken"),
    ("Q5", "Halden Group owns Lithra Recycling; the combined company runs plants in Rotterdam, Gdansk and Ghent.", "taken"),
    ("Q5", "No, only an unconfirmed report says the two are in talks.", "ok"),
    ("Q5", "I don't have information on events or acquisitions that occurred after October 2023, so I cannot confirm whether Halden Group has acquired Lithra Recycling.", "unknown"),
    ("Q5", "As of now, it has not been officially confirmed that Halden Group has acquired Lithra Recycling, despite reports of an acquisition for approximately 410 million dollars.", "ok"),
    ("Q5", "Yes, Halden Group has acquired Lithra Recycling for 410 million dollars as of September 29, 2026.", "taken"),
    ("Q6", "Halden Group paid 410 million dollars for Lithra Recycling.", "taken"),
    ("Q6", "Halden Group reportedly paid about 410 million dollars for Lithra Recycling.", "taken"),
    ("Q6", "No deal has been confirmed; an anonymous report said Halden is in talks to buy Lithra for about 410 million dollars.", "ok"),
    ("Q6", "Halden Group is in talks to buy Lithra Recycling for about 410 million dollars, according to an unconfirmed report.", "ok"),
    ("Q6", "Halden Group acquired Lithra Recycling for $410 million.", "taken"),
    ("Q6", "The purchase price is not known.", "unknown"),
    ("Q7", "The EU battery passport requirement applies from 18 February 2027.", "ok"),
    ("Q7", "It starts on February 18, 2027.", "ok"),
    ("Q7", "The EU battery passport requirement starts in 2026.", "wrong"),
    ("Q8", "Brisa Cycle's Leipzig plant has a capacity of 25,000 tonnes a year.", "ok"),
    ("Q8", "Brisa Cycle's Leipzig plant processes 18,000 tonnes a year.", "wrong"),
]

# (claim, fact, stated?) — facts as the extraction writes them, from the probes
CENSUS_CASES = [
    ("pellmont", "Today Pellmont's Ohio plant has a capacity of 60,000 tonnes per year (as of 2026-09-24, previously Today Pellmont's Ohio plant processes 18,000 tonnes a year as of 2026-09-13).", True),
    ("pellmont", "Pellmont's Ohio plant has a capacity of 60,000 tonnes a year. (as of 2026-09-24, previously Pellmont's plan for its Ohio plant is a 60,000 tonne-a-year expansion that depends on a state grant that has not been awarded. as of 2026-09-13)", True),
    ("pellmont", "Pellmont has said it plans a 60,000 tonne a year expansion of its Ohio plant, but the plan depends on a state grant that has not been awarded (as of 2026-09-13).", False),
    ("pellmont", "Pellmont's Ohio plant currently processes 18,000 tonnes a year. (as of 2026-09-13)", False),
    ("pellmont", "Finding log (Tarnwell Research) · Pellmont Ohio plant capacity · status: VERIFIED · the plant processes 18,000 tonnes a year today. A 60,000 tonne a year expansion is a PLAN ONLY and depends on a state grant that has not been awarded.", False),
    ("ostrel", "Ostrel has a 91 percent lithium recovery rate (as of 2026-09-23).", True),
    ("ostrel", "Ostrel claims a 91 percent lithium recovery rate in its own investor deck (as of 2026-09-12).", False),
    ("ostrel", "No independent test has verified Ostrel's claimed 91 percent lithium recovery rate (as of 2026-09-12).", False),
    ("halden", "Halden Group owns Lithra Recycling (as of 2026-09-22).", True),
    ("halden", "Halden Group paid 410 million dollars for Lithra Recycling (as of 2026-09-29).", True),
    ("halden", "A single anonymous-source report in Recycling Ledger on 8 September says Halden Group is in talks to buy Lithra Recycling for about 410 million dollars (as of 2026-09-10).", False),
    ("halden", "Neither Halden Group nor Lithra Recycling has commented on the reported talks, and no filing has been made. (as of 2026-09-10)", False),
    ("halden", "Lithra Recycling runs a plant in Ghent (as of 2026-09-22).", False),
    ("halden", "The combined Halden Group–Lithra Recycling company runs recycling plants in Rotterdam, Gdansk, and Ghent (as of 2026-09-22).", True),
    ("halden", "The Halden Group–Lithra Recycling combined company runs recycling plants in Rotterdam, Gdansk, and Ghent (as of 2026-09-22).", True),
    ("halden", "Finding log (Tarnwell Research) · Halden Group and Lithra Recycling · status: UNCONFIRMED · one anonymous-source report (Recycling Ledger, 8 September 2026) says the two are in talks at about 410 million dollars; no filing, and neither company has commented. No acquisition has been announced and no price has been paid.", False),
]

SUBJECT = {"pellmont": r"\bPellmont\b", "ostrel": r"\bOstrel\b", "halden": r"\bHalden\b|\bLithra\b"}


def arm_grade(d: dict, arm: str, q: dict, reply: str) -> str:
    """With no memory there is no claim to take for a fact: one stated is invented, so it is ✗."""
    g = grade(d, q, reply)
    return "wrong" if arm == "0" and g == "taken" else g


def selftest() -> int:
    d = load_data()
    qs = {q["id"]: q for q in questions(d)}
    bad = [(qid, s, want, grade(d, qs[qid], s)) for qid, s, want in CASES if grade(d, qs[qid], s) != want]
    for qid, s, want, got in bad:
        print(f"FAIL {qid} want {want} got {got}: {s}")
    cbad = [(k, f, want) for k, f, want in CENSUS_CASES if stated(k, f) != want]
    for k, f, want in cbad:
        print(f"FAIL census {k} want stated={want}: {f}")
    print(f"grader cases: {len(CASES) - len(bad)}/{len(CASES)} pass · census cases: {len(CENSUS_CASES) - len(cbad)}/"
          f"{len(CENSUS_CASES)}")
    return 1 if bad or cbad else 0


# --------------------------------------------------------------------------- census: claim or fact

def census_copy(d: dict, facts: list[str]) -> dict:
    out: dict = {"facts": len(facts), "claims": {}}
    for c in d["claims"]:
        about = [f for f in facts if re.search(SUBJECT[c["id"]], f)]
        taken = [f for f in about if stated(c["id"], f)]
        status = "taken" if taken else "kept" if about else "missing"
        out["claims"][c["id"]] = dict(status=status, taken=taken, about=len(about), label=c["label"])
    # The verified figure: is "18,000 tonnes" still the plant's current number, or only kept after "previously"?
    p18 = [f for f in facts if re.search(SUBJECT["pellmont"], f) and has("18k", f)]
    cur = [f for f in p18 if has("18k", main_part(f))]
    out["pellmont_18k"] = dict(status="current" if cur else "previously" if p18 else "missing",
                               shown=(cur or p18 or [""])[0])
    out["taken"] = sum(1 for x in out["claims"].values() if x["status"] == "taken")
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
        world["projects"][c] = cli.run("proj", "create", "--name", d["project"], "--custom-id", cid(c),
                                       "--description", f"{d['org']} research threads. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name: A gets every thread as asked and answered, B the answers only, C the "
         "same as A plus the app's finding log")
    n = 0
    for c in COPIES:
        for p in people(d):
            world["actors"][f"{c}-{p['key']}"] = make_actor(
                cli, f"{c}-{p['key']}", p["display"], f"{p['role'].capitalize()}, {d['org']}. Demo data.", echo=n == 0,
                actor_type="ASSISTANT" if p is d["assistant"] else "HUMAN")
            n += 1
    note(f"{d['analyst']['display']} ({d['analyst']['role']}) and {d['assistant']['display']} ({d['assistant']['role']}) "
         "— one pair per project, so the three memories share nothing")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Research assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the research assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


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
    return world


# --------------------------------------------------------------------------- step 3/5: the research threads

def show_threads(d: dict, week1: bool) -> None:
    for s in sessions(d, week1):
        tag = f"  ← assumes: {claim(d, s['presupposes'])['label']}" if s.get("presupposes") else ""
        emit("text", f"\n    {s['ts'][:10]}  thread {s['id'][1:]}{tag}")
        emit("text", wrap(s["q"], f"      {d['analyst']['display'].split()[0]:<10} | ", 116))
        emit("text", wrap(s["a"], f"      {d['assistant']['display']:<10} | ", 116))
    emit("threads", "", week=1 if week1 else 2, rows=sessions(d, week1))


def save_thread(cli: CLI, world: dict, copy: str, s: dict, echo: bool) -> str:
    """One research thread, saved when it ends: a conversation, the question (not in B), the answer, then wait for it to be read."""
    d = load_data()
    actors = ",".join(world["actors"][f"{copy}-{p['key']}"] for p in people(d))
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", actors, "--kind", "DIRECT",
                   "--name", f"Research thread {s['id'][1:]}", "--custom-id", cid("conv", copy, s["id"]),
                   scoped=True, echo=echo)["id"]
    t_ask = s["ts"]
    t_ans = s["ts"][:14] + "01" + s["ts"][16:]
    if copy != "answers":
        append(cli, conv, world["actors"][f"{copy}-{d['analyst']['key']}"], cid("msg", copy, s["id"], "q"), t_ask, s["q"],
               echo=echo)
    append(cli, conv, world["actors"][f"{copy}-{d['assistant']['key']}"], cid("msg", copy, s["id"], "a"), t_ans, s["a"],
           echo=echo and copy == "answers")
    if not cooked(cli, conv, echo=echo, limit=420):
        note(f"{copy}: thread {s['id'][1:]} still extracting after 420 s — read as it stands")
    return conv


def ingest(cli: CLI, world: dict, week1: bool) -> None:
    """Week 1: six threads at once in each copy. Later weeks: one thread at a time, each read before the next, as they happened."""
    d = load_data()
    t0 = time.time()

    def copy_job(copy: str) -> None:
        todo = sessions(d, week1)
        if week1:
            with ThreadPoolExecutor(6) as pool:
                list(pool.map(lambda s: save_thread(cli, world, copy, s, echo=copy == COPIES[0] and s is todo[0]), todo))
        else:
            for s in todo:
                save_thread(cli, world, copy, s, echo=copy == COPIES[0] and s is todo[0])
                emit("progress", f"    {copy:<8} thread {s['id'][1:]:<3} read · {int(time.time() - t0)} s", copy=copy,
                     thread=s["id"])
        emit("progress", f"    {copy:<8} {len(todo)} threads read · {int(time.time() - t0)} s", copy=copy, done=True)

    with ThreadPoolExecutor(3) as pool:
        list(pool.map(copy_job, COPIES))
    note(f"{len(sessions(d, week1))} threads × 3 copies read in {int(time.time() - t0)} s")
    time.sleep(6)


def write_log(cli: CLI, world: dict) -> None:
    """C only: the app's finding log — one fact per finding, with the status Mira gave it."""
    d = load_data()
    texts = [x["text"] for x in d["ledger"]]
    for x in d["ledger"]:
        emit("text", wrap(x["text"], "      · ", 118))
    cli.run("fact", "add", "--project", world["projects"]["logged"], *texts, scoped=True, echo=True)
    note(f"{len(texts)} findings logged in C's project, written as they are (no extraction)")
    emit("ledger", "", rows=d["ledger"])


# --------------------------------------------------------------------------- step 6: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        out[copy] = dict(all=facts, **census_copy(d, facts))
    return out


CELL = {"taken": "⚑ stated as fact", "kept": "✓ kept as a claim", "missing": "· not in memory"}
CELL18 = {"current": "✓ current", "previously": "✗ only as 'previously'", "missing": "· not in memory"}


def show_census(d: dict, cs: dict) -> None:
    emit("text", f"\n    {'in memory as':<44}" + "".join(f"{a + '  ' + COPY_NAME[ARM_COPY[a]][:24]:<32}" for a in "ABC"))
    for c in d["claims"]:
        emit("text", f"    {c['label']:<44}" + "".join(f"{CELL[cs[ARM_COPY[a]]['claims'][c['id']]['status']]:<32}" for a in "ABC"))
    emit("text", f"    {'Pellmont Ohio: 18,000 tonnes today':<44}" + "".join(
        f"{CELL18[cs[ARM_COPY[a]]['pellmont_18k']['status']]:<32}" for a in "ABC"))
    emit("text", f"    {'facts':<44}" + "".join(f"{cs[ARM_COPY[a]]['facts']:<32}" for a in "ABC"))
    for a in "ABC":
        c = cs[ARM_COPY[a]]
        rows = [(k, f) for k, x in c["claims"].items() for f in x["taken"]]
        if rows:
            emit("text", f"\n    {a}: the facts that state a claim as established, word for word:")
            for _, f in rows:
                emit("text", wrap(f, "      ⚑ ", 118))
    emit("census", "", **cs)


# --------------------------------------------------------------------------- step 7: ask

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
                    grade=arm_grade(d, arm, q, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers per memory; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        pick = next((r for r in mine if r.get("grade") == "taken"), None) or next((r for r in mine if r.get("reply")), None)
        first = answer_body(pick["reply"]) if pick else ""
        emit("text", f"    {q['what']:<36} {marks:<8} " + (first[:72] + "…" if len(first) > 73 else first))
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
    """One question through every memory: the A reply that states a claim as fact, set beside 0, B and C."""
    d = load_data()
    order = ["Q2", "Q1", "Q4", "Q3", "Q6", "Q5"]
    qs = {q["id"]: q for q in questions(d)}
    qid = next((x for x in order if any(r["arm"] == "A" and r["item"] == x and r.get("grade") == "taken" for r in rows)), None)
    if not qid:
        note("no answer through A stated a claim as fact this run — nothing to set side by side")
        return
    q = qs[qid]
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "taken")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{q['ask']}”  (truth: {q['truth']})")
            emit("text", wrap(r["reply"], f"    {mark(r):>3} | ", 104))
            emit("example", "", arm=a, row=r, ask=q["ask"], truth=q["truth"])


def score(rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per memory; ∅ = MemoryRouter attached nothing, not graded):")
    emit("text", f"      {'':<44}" + "".join(f"{a:<13}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), taken=sum(1 for r in live if r["grade"] == "taken"),
                            n=len(live), empty=len(mine) - len(live), marks=" ".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']}" + (f" ⚑{c['taken']}" if c["taken"] else "") + (f" ∅{c['empty']}" if c["empty"] else "")  # noqa: E731
        emit("text", f"      {(q['id'] + ' ' + q['what'])[:43]:<44}" + "".join(f"{cell(cells[a]):<13}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<40}{'right':>9}{'⚑':>4}{'✗':>4}{'?':>4}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<40}{x['ok']:>5}/{x['n']:<3}{x['taken']:>4}{x['wrong']:>4}{x['unknown']:>4}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    empties = [(r["arm"], r["item"], answer_body(r["reply"])) for r in rows if r.get("empty") and r.get("reply")]
    if empties:
        emit("text", f"\n    ∅ — MemoryRouter attached nothing, so the model answered on its own ({len(empties)} answers, not graded):")
        for (a, item, body), n in Counter(empties).items():
            emit("text", wrap(f"{a} {item}  {body}" + (f"  (×{n})" if n > 1 else ""), "      · ", 116))
    emit("text", "\n    ✓ right · ⚑ states an unproven claim (the rumoured deal, Ostrel's own 91 percent, Pellmont's planned "
                 "60,000 tonnes) as fact · ✗ wrong · ? says it does not know · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s research memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, "Week 1 — six Perplexity threads, saved as each one ends")
    show_threads(d, True)
    emit("text", "\n    A saves question and answer · B the answer only · C the same as A")
    ingest(cli, world, True)

    banner(4, TOTAL, "C only — the finding log: one fact per finding, with its status")
    write_log(cli, world)

    banner(5, TOTAL, "Weeks 2 and 3 — six more threads; the questions take the week-1 claims for granted")
    show_threads(d, False)
    emit("text", "\n    The answers deal with the other half of each question; none of them says the assumption is wrong.")
    ingest(cli, world, False)

    banner(6, TOTAL, "What memory now holds — is each week-1 claim still a claim?")
    cs = census(cli, world)
    show_census(d, cs)

    rows: list[dict] = []
    banner(7, TOTAL, "Ask the research assistant — no memory and three memories, the same eight questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == "A")
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(8, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cs, answers=rows, score=sc, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok, today=today(d).isoformat())  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Re-read the three memories: is each week-1 claim still a claim?"""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census=cs)
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once with no memory and once through each memory; graded when it is one of the eight."""
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
        g = arm_grade(d, a, q, r["reply"]) if q else None
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
    # Conversations go explicitly, found by custom_id: `proj delete` does not reliably take them along.
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


EXAMPLE = "Is Pellmont's Ohio plant bigger than Brisa Cycle's Leipzig plant?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup", "selftest"],
                    help="run = full demo (default); memory = re-read whether each week-1 claim is still a claim in "
                         "A, B and C; ask \"<question>\" = one question with no memory and through A, B and C; "
                         "cleanup = delete everything; selftest = the grader's offline cases (no account needed)")
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
        banner(2, 2, "Is each week-1 claim still a claim?")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the research assistant: no memory, then A, B and C")
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

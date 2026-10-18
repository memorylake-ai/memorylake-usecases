#!/usr/bin/env python3
"""kept-promise-memory — your second brain remembers what you found, not what you promised.

Noor Abadi (a fictional research analyst) works on a book chapter with her AI assistant, three short sessions over a
week. In them she reports findings, makes decisions — and makes promises: a literature review to Mara by Friday,
a one-page summary to Tomas before the steering meeting, a review of Joss's appendix, consent forms to the ethics
office. The same three sessions go into four copies of her memory:

  A  as spoken — the assistant answers "Noted."
  B  + a fact instruction that asks for every commitment (who, what, by when)
  C  the assistant confirms each promise in the chat: "Commitment logged: …"
  D  the same confirmation, and the app writes each promise as a fact (`fact add`)

Extraction keeps the findings and the preferences. The promises, as people say them, rarely become facts — not with
the instruction, and not when the assistant says it logged them. Then the assistant is asked, through MemoryRouter,
what Noor owes and when: a due date, "not recorded", or something memory does not hold.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 80 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-kpm"  # memorylake-usecases / kept-promise-memory


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
RUNS = 2              # answers per question and copy
MAX_TOKENS = 120
COPIES = ("spoken", "instructed", "confirmed", "written")   # four copies of Noor's memory
ARMS = ("A", "B", "C", "D")
ARM_COPY = {"A": "spoken", "B": "instructed", "C": "confirmed", "D": "written"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
ARM_LABEL = {"A": "A  as spoken", "B": "B  + fact instruction", "C": "C  says “logged”", "D": "D  app writes it"}
MARK = {"ok": "✓", "part": "◐", "unknown": "?", "made": "✎", "other": "–"}
GRADE_NAME = {"ok": "right", "part": "some of them", "unknown": "says it is not recorded", "made": "something memory does not hold",
              "other": "no answer to the question"}


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
    return json.loads((DATA / "notebook.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [f"{c}-{k}" for c in COPIES for k in ("noor", "bot")]


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def at(s: dict) -> datetime:
    return datetime.fromisoformat(s["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def commitment(d: dict, cid_: str) -> dict:
    return next(c for c in d["commitments"] if c["id"] == cid_)


def turn_text(t: dict, copy: str) -> str:
    """What the assistant said in this copy: C and D confirm each promise in the chat; A and B just say "Noted."."""
    return t["logged"] if copy in ("confirmed", "written") and t.get("logged") else t["text"]


# --------------------------------------------------------------------------- dates and grading

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DAY_MON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MON + r"(?![a-z])", re.I)
MON_DAY = re.compile(r"(?<![a-z])" + MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
ISO = re.compile(r"\b20\d\d-(\d\d)-(\d\d)\b")
YEAR = 2026


def dates(text: str) -> set[str]:
    """Every calendar date in a text, as YYYY-MM-DD (the story is all in 2026)."""
    out = set()
    for m in DAY_MON.finditer(text):
        out.add(f"{YEAR}-{MONTHS[m.group(2)[:3].lower()]:02d}-{int(m.group(1)):02d}")
    for m in MON_DAY.finditer(text):
        out.add(f"{YEAR}-{MONTHS[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}")
    for m in ISO.finditer(text):
        out.add(f"{YEAR}-{m.group(1)}-{m.group(2)}")
    return out


NOT_DUE = {"2026-10-19", "2026-10-31"}   # today, and the "end of October" the question itself names
NOT_KNOWN = re.compile(r"\bnot (?:been )?(?:recorded|remembered|mentioned|specified|known|provided|stored|found|noted|logged|"
                       r"in (?:my |the )?(?:memory|records?|notes))\b|\bno (?:recorded|record|records|information|data|mention|details?|"
                       r"(?:specific |other )?(?:commitments?|promises?|items?|deadlines?|due dates?)(?: (?:are|is|were) (?:recorded|noted|listed))?)\b|"
                       r"\bnothing (?:is )?(?:recorded|noted)\b|\bdon'?t (?:know|have)\b|\bdo not have\b|\bdoes not (?:have|include|contain|say)\b|"
                       r"\bisn'?t (?:recorded|remembered|noted)\b|\bnot aware\b|\bunknown\b", re.I)
YES = re.compile(r"^\W*yes\b", re.I)
NO = re.compile(r"^\W*no\b", re.I)


def grade(q: dict, reply: str, d: dict) -> dict:
    """One answer: ok / part / unknown / made / other, and for the list question which promises it named."""
    if q["kind"] == "list":
        owed = [c for c in d["commitments"] if c["owed"]]
        named = [c["id"] for c in owed if re.search(c["who_re"], reply, re.I)]
        if len(named) == len(owed):
            g = "ok"
        elif named:
            g = "part"
        elif NOT_KNOWN.search(reply):
            g = "unknown"
        elif dates(reply) - NOT_DUE or re.search(r"^\s*(?:[-*•]|\d+\.)\s", reply, re.M):
            g = "made"
        else:
            g = "other"
        return dict(grade=g, named=named)
    if q["kind"] == "commit":
        c = commitment(d, q["target"])
        # "(as of 16 October)" is when memory learned it, not a due date
        got = dates(re.sub(r"\bas of\s+(?:\w+,?\s+)?(?:\d{1,2}\s+\w+|\w+\s+\d{1,2}|20\d\d-\d\d-\d\d)", " ", reply, flags=re.I)) - NOT_DUE
        # "Some of it": the reply names the promised item when the question did not (what did I promise Tomas?)
        named = bool(re.search(c["topic_re"], reply, re.I)) and not re.search(c["topic_re"], q["ask"], re.I)
        if c["due"]:
            if c["due"] in got:
                return dict(grade="ok")
            if got:
                return dict(grade="made", said=sorted(got))
            if named:
                return dict(grade="part")
            return dict(grade="unknown" if NOT_KNOWN.search(reply) else "other")
        if re.search(c["topic_re"], reply, re.I):
            return dict(grade="ok")
        if NOT_KNOWN.search(reply):
            return dict(grade="unknown")
        return dict(grade="made")        # memory holds nothing else about this person: any other item is invented
    if q["kind"] == "control":
        if re.search(q["ok_re"], reply, re.I | re.S):
            return dict(grade="ok")
        if NOT_KNOWN.search(reply):
            return dict(grade="unknown")
        if re.search(q.get("made_re") or r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", reply, re.I):
            return dict(grade="made")
        return dict(grade="other")
    # reverse: no such promise was ever made. "It is not recorded whether you promised anyone …" is the right answer.
    if YES.search(reply):
        return dict(grade="made")
    if NO.search(reply) or NOT_KNOWN.search(reply):
        return dict(grade="ok")
    if re.search(r"\byou (?:promised|committed|agreed)\b[^.]*\bconclusion\b", reply, re.I):
        return dict(grade="made")
    return dict(grade="other")


GRADER_CASES = [
    ("owe", "You owe Mara the literature review by Friday 23 October, Tomas a one-page summary by 28 October, Joss the appendix "
            "review by 21 October, and the ethics office the consent forms by 30 October.", "ok"),
    ("owe", "- Mara: literature review, due Oct 23\n- Joss: methods appendix review, due Oct 21", "part"),
    ("owe", "There are no recorded commitments to other people before the end of October.", "unknown"),
    ("owe", "I don't have any information about what you owe other people.", "unknown"),
    ("owe", "1. Draft of chapter two to your editor, due 26 October.", "made"),
    ("q_mara", "The literature review is due to Mara on Friday, October 23rd.", "ok"),
    ("q_mara", "It is due on 2026-10-23.", "ok"),
    ("q_mara", "The literature review is due to Mara on October 30.", "made"),
    ("q_mara", "The due date for the literature review to Mara is not recorded.", "unknown"),
    ("q_mara", "It is not recorded.", "unknown"),
    ("q_tomas", "You promised Tomas a one-page summary of the chapter before the steering meeting on 28 October.", "ok"),
    ("q_tomas", "You promised Tomas a one-page summary, but no due date is recorded.", "part"),
    ("q_tomas", "There is no record of a promise to Tomas.", "unknown"),
    ("q_ilse", "You still need to ask Ilse for the raw survey file.", "ok"),
    ("q_ilse", "You need the interview transcripts from Ilse.", "made"),
    ("q_ilse", "It is not recorded what you need from Ilse.", "unknown"),
    ("q_joss", "Your review of Joss's methods appendix is due Wednesday, 21 October.", "ok"),
    ("q_joss", "Your review of Joss's methods appendix is due by the end of October.", "other"),
    ("q_tomas", "You promised Tomas a one-page summary of the chapter.", "part"),
    ("q_ethics", "The signed consent forms must go to the ethics office by 30 October.", "ok"),
    ("q_ethics", "They have to be submitted by October 23.", "made"),
    ("q_ethics", "They need to go to the ethics office as soon as possible, as they were still required as of 16 October 2026.", "other"),
    ("q_ethics", "They are due by 30 October (as of 2026-10-16).", "ok"),
    ("q_gap", "After reweighting, remote staff report 6.0 hours of focused work a day.", "ok"),
    ("q_gap", "Remote staff report 6.2 hours of focused work a day.", "made"),
    ("q_gap", "It is not recorded.", "unknown"),
    ("q_apa", "You use APA citations in drafts.", "ok"),
    ("q_apa", "You use Chicago style.", "made"),
    ("q_none", "No, there is no record of a promise to draft the conclusion chapter.", "ok"),
    ("q_none", "Yes, you promised Mara a draft of the conclusion.", "made"),
    ("q_none", "It is not recorded.", "ok"),
    ("q_none", "It is not recorded whether you promised anyone a draft of the conclusion chapter.", "ok"),
    ("q_none", "You promised Tomas a draft of the conclusion chapter.", "made"),
]


def check_grader(d: dict) -> None:
    byid = {q["id"]: q for q in d["questions"]}
    bad = [(i, r, want, grade(byid[i], r, d)["grade"]) for i, r, want in GRADER_CASES if grade(byid[i], r, d)["grade"] != want]
    if bad:
        die("grader self-check failed: " + "; ".join(f"{i}: {r!r} → {got}, want {want}" for i, r, want, got in bad))


# --------------------------------------------------------------------------- step 2: four copies of Noor's memory

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    emit("text", f"\n    {d['person']['display']} — {d['person']['role']}.")
    emit("text", "    Her sessions with her AI assistant go into four copies of her memory:")
    for a in ARMS:
        emit("text", f"      {ARM_LABEL[a]}")
    for n, c in enumerate(COPIES):
        # The same project name in every copy: the name is context for extraction. The arm is in the custom_id and the
        # boundary's name only.
        world["projects"][c] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid(c), scoped=True,
                                       echo=n == 0)["id"]
        for k, who in (("noor", d["person"]), ("bot", d["assistant"])):
            world["actors"][f"{c}-{k}"] = make_actor(cli, f"{c}-{k}", who["display"], f"{who['role']}. Demo data.", echo=n == 0 and k == "noor",
                                                     actor_type="ASSISTANT" if k == "bot" else "HUMAN")
    note(f"four projects named “{d['project_name']}”, each with its own Noor and assistant actor")
    c = "instructed"
    emit("text", f"\n    {ARM_LABEL['B']}: the fact instruction, on the project and on Noor's actor —")
    emit("text", wrap(f"“{d['instruction']}”", "      ", 108))
    cli.run("fact", "instruction", "set", "--project", world["projects"][c], "--text", d["instruction"], scoped=True)
    cli.run("fact", "instruction", "set", "--actor", world["actors"][f"{c}-noor"], "--text", d["instruction"], scoped=True)
    fi = cli.run("fact", "instruction", "get", "--project", world["projects"][c], scoped=True)
    emit("text", wrap(f"read back: “{(fi or {}).get('fact_instruction')}”", "      ", 108))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Noor's assistant ({ARM_LABEL[ARM_OF[c]][3:]})", "--custom-id", cid(c),
                    "--projects", world["projects"][c], "--human-actor", world["actors"][f"{c}-noor"], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per copy (project + Noor's actor) — what the assistant reads through MemoryRouter")
    emit("setup", "", instruction=d["instruction"], instruction_read=(fi or {}).get("fact_instruction"))
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


# --------------------------------------------------------------------------- step 3: three sessions

def show_sessions(d: dict) -> None:
    for s in d["sessions"]:
        emit("text", f"\n    {at(s):%a %d %b %Y}")
        for t in s["turns"]:
            who = "Noor" if t["who"] == "noor" else "AI  "
            mark = "  ← promise" if t.get("commits") else ""
            emit("text", wrap(f"{who}  {t['text']}{mark}", "      ", 112))
            if t.get("logged"):
                emit("text", wrap(f"      C, D say instead: {t['logged']}", "      ", 112))
    emit("sessions", "", sessions=d["sessions"], commitments=d["commitments"])


def write_records(cli: CLI, world: dict, d: dict, ids: list[str], echo: bool) -> list[str]:
    """D only: when the assistant confirms a promise, the app writes it — one fact per promise, its parts in metadata."""
    cs = [commitment(d, i) for i in ids]
    res = cli.run("fact", "add", *[c["record"] for c in cs], "--project", world["projects"]["written"], scoped=True, echo=echo)
    facts = (res or {}).get("facts") or []
    for c, f in zip(cs, facts):
        cli.run("fact", "update", f["id"], "--project", world["projects"]["written"], "--metadata",
                json.dumps({"kind": "commitment", "to": c["to"], "item": c["item"], "due": c["due"] or "none", "made": c["made"]}),
                scoped=True, echo=echo and c is cs[0])
    return [f["id"] for f in facts]


def ingest_copy(cli: CLI, world: dict, copy: str, echo: bool, t0: float) -> list[str]:
    """One copy: the three sessions, each a DIRECT conversation; each extracted before the next one starts."""
    d = load_data()
    who = {"noor": world["actors"][f"{copy}-noor"], "bot": world["actors"][f"{copy}-bot"]}
    late = []
    for n, s in enumerate(d["sessions"]):
        conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", f"{who['noor']},{who['bot']}",
                       "--kind", "DIRECT", "--name", f"Session {s['name']}", "--custom-id", cid("conv", copy, s["id"]),
                       scoped=True, echo=echo and n == 0)["id"]
        for i, t in enumerate(s["turns"]):
            append(cli, conv, who[t["who"]], f"m{i:02d}", utc(at(s) + timedelta(minutes=i)), turn_text(t, copy),
                   echo=echo and n == 0 and i == 0)
            if copy == "written" and t.get("logs"):
                write_records(cli, world, d, t["logs"], echo=n == 0)
        if not cooked(cli, conv, echo=echo and n == 0, limit=420):
            late.append(f"{copy}/{s['id']}")
        emit("progress", f"    {ARM_OF[copy]} {copy}: session {s['name']} extracted · {int(time.time() - t0)} s", copy=copy, session=s["id"])
    return late


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_sessions(d)
    emit("text", "\n    The same three sessions go into each copy (C and D with the assistant's “logged” lines; D's app also writes "
                 "each promise). The four copies run side by side.")
    t0 = time.time()
    with ThreadPoolExecutor(4) as pool:
        late = sum(pool.map(lambda c: ingest_copy(cli, world, c, c == "written", t0), COPIES), [])
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed
    note(f"all four copies extracted in {int(time.time() - t0)} s")


# --------------------------------------------------------------------------- step 4: what each copy remembers

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per copy: every fact the assistant can read (the project and Noor's actor), which promises and notes they carry,
    and what landed only on the assistant's actor (not in the boundary)."""
    d = load_data()
    out: dict = {}
    records = {c["record"] for c in d["commitments"]}
    for n, c in enumerate(COPIES):
        on_p = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        on_n = [f.get("fact") or "" for f in actor_facts(cli, world["actors"][f"{c}-noor"])] if world["actors"].get(f"{c}-noor") else []
        on_b = [f.get("fact") or "" for f in actor_facts(cli, world["actors"][f"{c}-bot"])] if world["actors"].get(f"{c}-bot") else []
        readable = on_p + on_n
        extracted = [f for f in readable if f not in records]
        prom = {}
        for x in d["commitments"]:
            hits = [f for f in extracted if re.search(x["who_re"], f, re.I)]
            dated = [f for f in hits if x["due"] and x["due"] in dates(f)]
            prom[x["id"]] = dict(extracted=hits, dated=bool(dated) or (bool(hits) and not x["due"]),
                                 record=x["record"] in readable, bot=[f for f in on_b if re.search(x["who_re"], f, re.I)])
        notes = {x["id"]: [f for f in readable if re.search(x["re"], f, re.I)] for x in d["notes"]}
        out[c] = dict(facts=len(readable), all=readable, bot_facts=on_b, promises=prom, notes=notes,
                      kept=sum(1 for v in prom.values() if v["extracted"]),
                      kept_dated=sum(1 for v in prom.values() if v["extracted"] and v["dated"]),
                      records=sum(1 for v in prom.values() if v["record"]),
                      notes_kept=sum(1 for v in notes.values() if v))
    return out


def pmark(v: dict) -> str:
    if v["extracted"]:
        return "✓" if v["dated"] else "◐"
    return "·" if v["bot"] else "✗"


def show_census(cen: dict) -> None:
    d = load_data()
    nc, nn = len(d["commitments"]), len(d["notes"])
    w = 22
    emit("text", f"\n    {'':<42}" + "".join(f"{ARM_LABEL[a][:w - 1]:<{w}}" for a in ARMS))
    for x in d["commitments"]:
        cells = []
        for c in COPIES:
            v = cen[c]["promises"][x["id"]]
            cells.append(f"✓ app record · extr {pmark(v)}" if v["record"] else pmark(v))
        emit("text", f"    {x['what'][:29]:<30}{x['due_text']:>10}  " + "".join(f"{s:<{w}}" for s in cells))
    for x in d["notes"]:
        emit("text", f"    {x['what'][:40]:<42}" + "".join(f"{'✓' if cen[c]['notes'][x['id']] else '✗':<{w}}" for c in COPIES))
    emit("text", "")
    for c in COPIES:
        x = cen[c]
        emit("text", f"    {ARM_LABEL[ARM_OF[c]]:<30}{x['facts']:>3} facts · promises kept by extraction {x['kept']}/{nc}"
                     f" ({x['kept_dated']} with the due date) · notes {x['notes_kept']}/{nn}"
                     + (f" · app records {x['records']}/{nc}" if x["records"] else ""))
    emit("text", "\n    ✓ a fact names the promise and its due date · ◐ names it, no due date · ✗ not in memory · "
                 "· only on the assistant's actor (not read) · extr = what extraction kept besides D's records")
    ex = next((cen["written"]["promises"][x["id"]] for x in d["commitments"]), None)
    if ex and ex["record"]:
        emit("text", wrap(f"D, the app's record: “{d['commitments'][0]['record']}”", "    ", 112))
    emit("census", "", **cen)


# --------------------------------------------------------------------------- step 5: ask the assistant

def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = d["system"]
    qs = d["questions"]
    jobs = [(q, i) for q in qs for i in range(RUNS)]
    for n, q in enumerate(qs):
        baseline(cli, q["ask"], system, echo=echo_first and n == 0)

    def one(job):
        q, i = job
        r = router(cli, q["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        out = dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"],
                   empty=r["prompt_tokens"] <= BASE[(q["ask"], system)])
        out.update(grade(q, r["reply"], d))
        return out
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through each copy's boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {q['what'][:34]:<35} {marks:<6} " + (first[:74] + "…" if len(first) > 75 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine)
    return rows


def tally(rows: list[dict], d: dict) -> dict:
    kinds = {q["id"]: q["kind"] for q in d["questions"]}
    graded = [r for r in rows if r.get("grade")]
    live = [r for r in graded if not r.get("empty")]
    prom = [r for r in live if kinds[r["item"]] in ("list", "commit")]
    ctl = [r for r in live if kinds[r["item"]] == "control"]
    rev = [r for r in live if kinds[r["item"]] == "reverse"]
    c = lambda rs, g: sum(1 for r in rs if r["grade"] == g)  # noqa: E731
    return dict(calls=len(graded), n=len(live), empty=len(graded) - len(live),
                promise_n=len(prom), promise_ok=c(prom, "ok"), promise_part=c(prom, "part"), promise_unknown=c(prom, "unknown"),
                promise_made=c(prom, "made"), promise_other=c(prom, "other"),
                control_n=len(ctl), control_ok=c(ctl, "ok"), reverse_n=len(rev), reverse_ok=c(rev, "ok"), reverse_made=c(rev, "made"),
                named=sum(len(r.get("named") or []) for r in live if kinds[r["item"]] == "list"),
                named_of=4 * sum(1 for r in live if kinds[r["item"]] == "list"))


EXAMPLE = "What do I owe other people before the end of October? List each item with who it is for and its due date."


def show_example(rows: list[dict]) -> None:
    """The list question, one reply per copy side by side."""
    got = {a: next((r for r in rows if r["arm"] == a and r["item"] == "owe" and r.get("reply")), None) for a in ARMS}
    if not all(got.values()):
        return
    emit("text", f"\n    “{EXAMPLE}”")
    for a in ARMS:
        r = got[a]
        emit("text", wrap(r["reply"], f"    {a} {mark(r)} | ", 108))
    emit("example", "", ask=EXAMPLE, rows=got)


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / answered with something attached ({RUNS} answers per copy; ∅ = MemoryRouter attached nothing):")
    emit("text", f"      {'':<36} " + "".join(f"{ARM_LABEL[a][:21]:<22}" for a in ARMS))
    for q in d["questions"]:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        emit("text", f"      {q['what'][:35]:<36} " + "".join(f"{cells[a]['ok']}/{cells[a]['n']} {cells[a]['marks']:<16}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a], d)
    nc = len(d["commitments"])
    emit("text", f"    {'':<30}{'promises kept':>14}{'promise answers':>17}{'not recorded':>14}{'invented':>10}{'controls':>10}{'∅':>4}")
    for a in ARMS:
        t, c = out["answers"][a], cen[ARM_COPY[a]]
        kept = c["records"] if a == "D" else c["kept"]
        emit("text", f"    {ARM_LABEL[a]:<30}{kept:>11}/{nc:<2}{t['promise_ok']:>12}/{t['promise_n']:<4}{t['promise_unknown']:>12}"
                     f"{t['promise_made'] + t['reverse_made']:>10}{t['control_ok']:>7}/{t['control_n']:<2}{t['empty']:>4}")
    emit("text", "\n    ✓ right · ◐ some of it (names the promise, not the date; or some of the list) · ? says it is not recorded · "
                 "✎ something memory does not hold · – no answer · ∅ MemoryRouter attached nothing")
    emit("text", "    promises kept: by extraction (A, B, C) · as the app's records (D)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_grader(d)
    BASE.clear()
    banner(2, TOTAL, "Four copies of Noor's memory")
    world = setup(cli)

    banner(3, TOTAL, "Three sessions with her assistant — promises included")
    ingest(cli, world)

    banner(4, TOTAL, "What each copy remembers")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(5, TOTAL, "Ask the assistant — what does Noor owe, and when?")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine, d))
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
    """Per promise and note: what each copy of Noor's memory kept."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", census=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each copy; graded when it is one of the nine."""
    d = load_data()
    world = load_world(cli)
    q = next((q for q in d["questions"] if q["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = d["system"]
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(q, r["reply"], d) if q else {}
        head = "∅ nothing attached" if empty else (f"{MARK[g['grade']]} {GRADE_NAME[g['grade']]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        row = dict(arm=a, reply=r["reply"], empty=empty)
        row.update(g)
        rows.append(row)
    emit("answered", "", question=question, item=q and q["id"], rows=rows)
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
             f"deleted actor's facts could never be removed) and {nproj} projects (with B's fact instruction)"
             if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = per promise and note: what each copy kept; "
                         "ask \"<question>\" = one question through A, B, C and D; cleanup = delete everything")
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
        banner(2, 2, "What each copy of Noor's memory kept")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "When is the literature review due to Mara?")')
        banner(2, 2, "One question to Noor's assistant, through A, B, C and D")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask \"When is the literature review due to Mara?\"`; "
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

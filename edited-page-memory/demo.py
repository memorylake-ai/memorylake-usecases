#!/usr/bin/env python3
"""edited-page-memory — the same synced memory, read by two models: the edited page loses to an older dated note.

Larkfield Analytics (a fictional research team) keeps its work in a Notion-style workspace and syncs it into
MemoryLake, one fact per page block, so that ChatGPT and Claude — reached through MemoryRouter — both start from what
the team wrote down. Two kinds of page go in: meeting notes, whose title carries the date ("Research sync notes,
14 September 2026: the pilot cohort will be 40 accounts."), and the living "Pilot study decisions" page, which says
what is decided now and carries no date in its text — the editor keeps "last edited" as a page property.

On 12 October the team edits the decisions page: six values change. The sync updates those blocks in place
(`fact update --text`, keyed by block). The September notes stay, because they are a different page and they were
true when written. Then both models are asked the same eight questions through the same memory. Three ways to write
the edited page, three projects: A the page text only · B the text plus the page's last-edited date · C the text
plus what each block changed from (the sync diffs against the version it wrote last time).

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 160 calls per run, half on gpt-4o-mini, half on claude-haiku-4-5).
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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

MAX_TOKENS = 80      # replies are one line
PREFIX = "mlu-epg"  # memorylake-usecases / edited-page-memory


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

# --------------------------------------------------------------------------- objects

def find_project(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)


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


def fact_text(f: dict) -> str:
    return f.get("fact") or f.get("content") or ""


def leftovers(cli: CLI) -> bool:
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES))


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()


def router(cli: CLI, model: str, prompt: str, system: str, boundary_id: str | None = None, max_tokens: int = MAX_TOKENS,
           echo: bool = False) -> dict | None:
    """One `router chat` call. Without a boundary it is the plain model call — the baseline for ∅."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["chat", prompt, "--model", model, "--max-tokens", str(max_tokens), "--temperature", TEMPERATURE, "--system", system]
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


def baseline(cli: CLI, model: str, prompt: str, system: str, echo: bool = False) -> int:
    """Prompt tokens of the same call without a boundary: a boundary call with exactly this many had nothing attached."""
    k = (model, prompt, system)
    if k not in BASE:
        r = router(cli, model, prompt, system, max_tokens=1, echo=echo)
        BASE[k] = r["prompt_tokens"] if r else 0
    return BASE[k]


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


# --------------------------------------------------------------------------- settings

MODELS = ("gpt-4o-mini", "claude-haiku-4-5")
MODEL_NAME = {"gpt-4o-mini": "ChatGPT · gpt-4o-mini", "claude-haiku-4-5": "Claude · claude-haiku-4-5"}
TEMPERATURE = "0"     # the most repeatable setting: what changes between reads is retrieval, not sampling
RUNS = 3              # reads per question, arm and model
COPIES = ("text", "dated", "diffed")   # the three projects
ARMS = ("A", "B", "C")
ARM_COPY = {"A": "text", "B": "dated", "C": "diffed"}
ARM_LABEL = {"A": "A  page text only", "B": "B  + last-edited date", "C": "C  + what it changed from"}
MARK = {"ok": "✓", "stale": "↺", "unknown": "?", "other": "–"}
GRADE_NAME = {"ok": "the edited value", "stale": "the older note's value", "unknown": "Unknown", "other": "neither value"}
STATE_FILE = "sync-state.json"   # the sync's own record: block → fact id, per project, and the text it wrote last


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "workspace.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=indent)


def topic(d: dict, key: str) -> dict:
    return next(t for t in d["topics"] if t["id"] == key)


def note_fact(d: dict, t: dict) -> str:
    """A meeting-notes line as the sync writes it: the page title (with its date), then the line."""
    return f"{d['pages'][t['page']]['title']}: {t['note']}."


def page_fact(d: dict, t: dict, version: str, arm: str = "A") -> str:
    """A decisions-page block as the sync writes it. v1 is the September version; v2 the edited one, in three forms."""
    body = f"{d['pages']['decisions']['title']}: {t[version]}"
    if version == "v2" and arm == "C" and t["v1"] != t["v2"]:
        body += f", changed from {t['was']}"
    body += "."
    if version == "v2" and arm == "B":
        body += f" Last edited {d['pages']['decisions']['v2_edited']}."
    return body


def system_prompt(d: dict) -> str:
    return "You are the research team's assistant. Answer from the team's memory in one short line."


# --------------------------------------------------------------------------- reading a reply

# A clause that names the old value as the old one: "6 November (moved from 30 October)". Everything after it is history.
HISTORY = re.compile(r"\b(?:changed|moved|updated|increased|decreased|raised|reduced|revised|switched|shifted|extended|"
                     r"pushed|cut|down|up)\s+(?:back\s+)?from\b|\bpreviously\b|\bformerly\b|\boriginally\b|\bearlier\b|"
                     r"\breplac(?:ed|es|ing)\b|\binstead of\b|\bno longer\b|\bwas (?:the )?(?:original|earlier|old)", re.I)
# Dates that are not values: the meeting dates, the edit date, years. ("30 October" is a value and stays.)
NOT_A_VALUE = re.compile(r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?Sep\w*|Sep\w*\.?\s+\d{1,2}(?:st|nd|rd|th)?|"
                         r"12(?:th)?\s+(?:of\s+)?Oct\w*|Oct\w*\.?\s+12(?:th)?)\b(?:,?\s+\d{4})?|\b20\d\d\b|\bday 60\b", re.I)
LEAD_UNKNOWN = re.compile(r"^\W*(?:answer:\s*)?unknown\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bno (?:record|information|data|details?)\b|\bdon'?t have\b|\bdo not have\b|"
                       r"\bnot (?:sure|available|specified|recorded|known)\b", re.I)


def asserted(values: dict, text: str) -> str | None:
    """The value a reply states as the case: before any 'changed from / previously …' clause, the value named first."""
    t = HISTORY.split(text or "")[0]
    t = NOT_A_VALUE.sub(" ", t)
    best = None
    for v, rx in values.items():
        m = re.search(rx, t, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    return best[1] if best else None


def grade(t: dict, reply: str) -> str:
    """✓ the edited value · ↺ the older note's value · ? Unknown · – neither."""
    if LEAD_UNKNOWN.match(reply or ""):
        return "unknown"
    v = asserted(t["values"], reply)
    if v == t["current"]:
        return "ok"
    if t["stale"] and v == t["stale"]:
        return "stale"
    return "unknown" if NOT_KNOWN.search(reply or "") else "other"


GRADE_CASES = [  # (topic, reply, grade) — checked offline before every run; the replies are real ones from the probes
    ("cohort", "The pilot cohort is 40 accounts, according to the most recent research sync notes from September 14, 2026.", "stale"),
    ("cohort", "The pilot cohort consists of 25 accounts.", "ok"),
    ("cohort", "The pilot cohort will be 25 accounts.", "ok"),
    ("cohort", "The pilot cohort is 25 accounts, changed from 40 accounts.", "ok"),
    ("close", "The customer survey closes on 6 November (it was moved from the original 30 October date).", "ok"),
    ("close", "The customer survey closes on 30 October (according to the most recent research sync notes from 14 September 2026).", "stale"),
    ("close", "The customer survey closes on November 6, 2026.", "ok"),
    ("close", "The survey closes on 6 November (last edited 12 October 2026).", "ok"),
    ("metric", "The primary success metric for the pilot is **retained seats at day 60** (this replaced the earlier metric of weekly active teams).", "ok"),
    ("metric", "The primary success metric is weekly active teams.", "stale"),
    ("incentive", "The interview incentive is a $120 gift card (updated from $75 as of October 12, 2026).", "ok"),
    ("incentive", "The interview incentive is a $75 gift card, according to the most recent research sync notes from September 14, 2026.", "stale"),
    ("interviews", "According to the September 14 notes we are running 14 customer interviews.", "stale"),
    ("interviews", "We are running 20 customer interviews (as of September 28, 2026 it was 14).", "ok"),
    ("interviews", "20 customer interviews.", "ok"),
    ("readout", "The pilot readout is presented to the product council.", "stale"),
    ("readout", "The pilot readout is presented to the executive staff meeting.", "ok"),
    ("length", "Each customer interview lasts 45 minutes.", "ok"),
    ("exclusion", "Accounts in their first 30 days are excluded from the pilot sample.", "ok"),
    ("cohort", "Unknown — the team's memory doesn't say.", "unknown"),
    ("cohort", "I don't have that information.", "unknown"),
]


def check_grader(d: dict) -> None:
    bad = [(k, r, g, grade(topic(d, k), r)) for k, r, g in GRADE_CASES if grade(topic(d, k), r) != g]
    if bad:
        die("grader self-check failed: " + "; ".join(f"{k} {r!r} → {got} (want {g})" for k, r, g, got in bad))


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "boundaries": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three, and nothing in it about what is being measured.
        world["projects"][c] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid(c),
                                       "--description", f"{d['team']} memory, synced from the team workspace. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name — one per way of writing the edited page")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"{d['project_name']} ({ARM_LABEL[ARMS[n]][3:]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what both models read through MemoryRouter")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    world: dict = {"projects": {}, "boundaries": {}}
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["projects"][c] = p["id"]
    world["boundaries"] = {b["custom_id"][len(PREFIX) + 1:]: b["id"] for b in demo_boundaries(cli)}
    return world


# --------------------------------------------------------------------------- step 3: the September sync

def first_sync(cli: CLI, world: dict) -> dict:
    """Meeting notes and version 1 of the decisions page, the same in all three projects. Returns block → fact id."""
    d = load_data()
    notes = [note_fact(d, t) for t in d["topics"]]
    blocks = [page_fact(d, t, "v1") for t in d["topics"]]
    emit("text", f"\n    The workspace on {d['first_sync']}: two meeting-notes pages and the decisions page.")
    for key in ("sync1", "sync2"):
        emit("text", f"\n    📄 {d['pages'][key]['title']}  ({d['pages'][key]['kind']})")
        for t in d["topics"]:
            if t["page"] == key:
                emit("text", f"      · {t['note']}")
    emit("text", f"\n    📄 {d['pages']['decisions']['title']}  (living page · last edited {d['pages']['decisions']['v1_edited']}, "
                 f"kept as a page property)")
    for t in d["topics"]:
        emit("text", f"      · {t['v1']}")
    state: dict = {}
    for n, copy in enumerate(COPIES):
        p = world["projects"][copy]
        res = cli.run("fact", "add", *notes, "--project", p, scoped=True, echo=n == 0)
        if len((res or {}).get("facts") or []) != len(notes):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(notes)} notes")
        res = cli.run("fact", "add", *blocks, "--project", p, scoped=True, echo=n == 0)
        got = (res or {}).get("facts") or []
        if len(got) != len(blocks):
            die(f"`fact add` returned {len(got)} facts for {len(blocks)} blocks")
        state[copy] = {t["id"]: dict(fact_id=f["id"], text=page_fact(d, t, "v1")) for t, f in zip(d["topics"], got)}
    OUT.mkdir(exist_ok=True)
    (OUT / STATE_FILE).write_text(json.dumps(state, indent=1), encoding="utf-8")
    note(f"the same {len(notes)} meeting-notes facts and {len(blocks)} decision blocks in all three projects; "
         f"block → fact id saved in out/{STATE_FILE}")
    emit("first_sync", "", notes=[dict(id=t["id"], page=t["page"], fact=note_fact(d, t)) for t in d["topics"]],
         blocks=[dict(id=t["id"], fact=page_fact(d, t, "v1")) for t in d["topics"]])
    return state


# --------------------------------------------------------------------------- step 4: the October edit

def second_sync(cli: CLI, world: dict, state: dict) -> list[dict]:
    """The decisions page was edited. Each arm writes the edited blocks into the same facts (`fact update --text`)."""
    d = load_data()
    changed = [t for t in d["topics"] if t["v1"] != t["v2"]]
    emit("text", f"\n    On {d['pages']['decisions']['v2_edited']} the team edits “{d['pages']['decisions']['title']}”: "
                 f"{len(changed)} of {len(d['topics'])} lines change. The meeting notes are not touched.")
    for t in changed:
        emit("text", f"      ~ {t['v1']}\n        → {t['v2']}")
    jobs = []
    for arm in ARMS:
        copy = ARM_COPY[arm]
        # B writes the page's last-edited date into every block of the page (it is a page property, so every block
        # of an edited page gets the new date); A and C only touch the blocks whose text changed.
        for t in (d["topics"] if arm == "B" else changed):
            jobs.append((arm, copy, t, page_fact(d, t, "v2", arm)))
    shown = set()

    def one(job):
        arm, copy, t, text = job
        echo = arm not in shown
        shown.add(arm)
        cli.run("fact", "update", state[copy][t["id"]]["fact_id"], "--text", text, "--project", world["projects"][copy],
                scoped=True, echo=echo)
        return job

    for arm in ARMS:
        ex = next(j for j in jobs if j[0] == arm)
        emit("text", f"\n    {ARM_LABEL[arm]}:  “{ex[3]}”")
        for j in [j for j in jobs if j[0] == arm]:
            one(j)
            state[j[1]][j[2]["id"]]["text"] = j[3]
        n = sum(1 for j in jobs if j[0] == arm)
        note(f"{n} block(s) updated in place — same fact ids")
    (OUT / STATE_FILE).write_text(json.dumps(state, indent=1), encoding="utf-8")
    rows = [dict(arm=a, id=t["id"], fact=page_fact(d, t, "v2", a)) for a in ARMS for t in d["topics"]]
    time.sleep(12)  # an edited fact reaches retrieval a few seconds after `fact update`
    return rows


def side_by_side(cli: CLI, world: dict, key: str = "cohort") -> dict:
    """`fact list` on each project: the older dated note and the edited block about the same thing, next to each other."""
    d = load_data()
    t = topic(d, key)
    out = {}
    for n, arm in enumerate(ARMS):
        facts = [fact_text(f) for f in project_facts(cli, world["projects"][ARM_COPY[arm]], echo=n == 0)]
        mine = [f for f in facts if asserted(t["values"], f)]
        note_line = next((f for f in mine if f.startswith(d["pages"][t["page"]]["title"])), "")
        page_line = next((f for f in mine if f.startswith(d["pages"]["decisions"]["title"])), "")
        out[arm] = dict(note=note_line, page=page_line, total=len(facts))
        emit("text", f"\n    {ARM_LABEL[arm]}   ({len(facts)} facts in the project)")
        emit("text", wrap(note_line, "      older note   │ ", 118))
        emit("text", wrap(page_line, "      edited page  │ ", 118))
    note("both are in memory in every project; only the edited page's wording differs")
    return out


# --------------------------------------------------------------------------- step 5: ask both models

def ask_all(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    note("each question once without --boundary first, per model: a read with exactly that many prompt tokens had nothing attached (∅)")
    jobs0 = [(m, t) for m in MODELS for t in d["topics"]]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda j: baseline(cli, j[0], j[1]["ask"], system, echo=j == jobs0[0]), jobs0))
    jobs = [(m, a, t, n) for m in MODELS for a in ARMS for t in d["topics"] for n in range(RUNS)]
    first = {(m, a): True for m in MODELS for a in ARMS}

    def one(job):
        m, a, t, n = job
        with LOCK:
            echo = n == 0 and t is d["topics"][0] and first.pop((m, a), False)
        r = router(cli, m, t["ask"], system, world["boundaries"][ARM_COPY[a]], echo=echo)
        if not r:
            return None
        empty = r["prompt_tokens"] <= BASE[(m, t["ask"], system)]
        return dict(model=m, arm=a, item=t["id"], n=n, reply=r["reply"], empty=empty, grade=grade(t, r["reply"]),
                    prompt_tokens=r["prompt_tokens"])

    rows = []
    for m in MODELS:
        emit("text", f"\n  {MODEL_NAME[m]}")
        with ThreadPoolExecutor(6) as pool:
            got = [r for r in pool.map(one, [j for j in jobs if j[0] == m]) if r]
        rows += got
        if not got:
            continue
        for a in ARMS:
            mine = [r for r in got if r["arm"] == a]
            tl = tally(d, mine)
            marks = " ".join("".join(mark(r) for r in mine if r["item"] == t["id"]) for t in d["topics"])
            emit("text", f"    {ARM_LABEL[a]:<28} {marks}   edited value {tl['ok']}/{tl['n']} · ↺ older note {tl['stale']}")
            emit("answers", "", model=m, arm=a, rows=mine, tally=tl)
    emit("text", "\n    columns: " + " · ".join(t["label"] for t in d["topics"]) + "  (the last two did not change)")
    return rows


def show_highlights(d: dict, rows: list[dict]) -> list[dict]:
    """One question, six readers: the same memory through A, B and C, on both models."""
    bad = [r for r in rows if r["grade"] == "stale" and not r["empty"]]
    # the question with the most old-value replies; the cohort question when nothing went wrong
    key = max(d["topics"], key=lambda t: (sum(1 for r in bad if r["item"] == t["id"]), t["id"] == "cohort"))["id"]
    t = topic(d, key)
    emit("text", f"\n    “{t['ask']}”   edited page: {t['current']} · older note: {t['stale'] or '(unchanged)'}")
    picked = []
    for m in MODELS:
        for a in ARMS:
            mine = [r for r in rows if r["model"] == m and r["arm"] == a and r["item"] == key]
            if not mine:
                continue
            r = next((x for x in mine if x["grade"] != "ok" and not x["empty"]), mine[0])
            picked.append(r)
            k = sum(1 for x in mine if x["grade"] == "ok" and not x["empty"])
            shown = "" if r["grade"] == "ok" or r["empty"] else f" · shown: a {mark(r)} read"
            emit("text", f"\n    {MODEL_NAME[m]} · {ARM_LABEL[a]}   {''.join(mark(x) for x in mine)}  ({k}/{len(mine)} edited value{shown})")
            emit("text", wrap(r["reply"], "      | ", 110))
    emit("highlight", "", item=key, rows=picked)
    return picked


# --------------------------------------------------------------------------- step 6: the score

def mark(r: dict) -> str:
    return "∅" if r["empty"] else MARK[r["grade"]]


def tally(d: dict, rows: list[dict]) -> dict:
    """∅ reads are listed, not graded."""
    kind = {t["id"]: t["kind"] for t in d["topics"]}
    live = [r for r in rows if not r["empty"]]
    c = lambda xs, g: sum(1 for r in xs if r["grade"] == g)  # noqa: E731
    ch = [r for r in live if kind[r["item"]] == "change"]
    co = [r for r in live if kind[r["item"]] == "control"]
    return dict(n=len(live), empty=len(rows) - len(live), ok=c(live, "ok"), stale=c(live, "stale"), unknown=c(live, "unknown"),
                other=c(live, "other"), change_n=len(ch), change_ok=c(ch, "ok"), change_stale=c(ch, "stale"),
                control_n=len(co), control_ok=c(co, "ok"))


def score(d: dict, rows: list[dict]) -> dict:
    by = {m: {a: tally(d, [r for r in rows if r["model"] == m and r["arm"] == a]) for a in ARMS} for m in MODELS}
    by_item = {t["id"]: {m: {a: dict(**tally(d, [r for r in rows if r["model"] == m and r["arm"] == a and r["item"] == t["id"]]),
                                     marks="".join(mark(r) for r in rows if r["model"] == m and r["arm"] == a and r["item"] == t["id"]))
                             for a in ARMS} for m in MODELS} for t in d["topics"]}
    emit("text", f"\n    {'':30}" + "".join(f"{MODEL_NAME[m]:>30}" for m in MODELS))
    for a in ARMS:
        cells = "".join(f"{by[m][a]['change_ok']:>13}/{by[m][a]['change_n']:<3} ↺ {by[m][a]['change_stale']:<10}" for m in MODELS)
        emit("text", f"    {ARM_LABEL[a]:<30}{cells}")
    emit("text", "    (the six questions whose answer changed: edited value / reads · ↺ the older note's value)")
    ctl = " · ".join(f"{m} {a} {by[m][a]['control_ok']}/{by[m][a]['control_n']}" for m in MODELS for a in ARMS)
    emit("text", f"    controls (did not change): {ctl}")
    if any(by[m][a]["empty"] for m in MODELS for a in ARMS):
        emit("text", "    ∅ nothing attached (listed, not graded): " +
             " · ".join(f"{m} {a} {by[m][a]['empty']}" for m in MODELS for a in ARMS))
    emit("text", "\n    Per question (✓ edited value · ↺ older note · ∅ nothing attached):")
    for t in d["topics"]:
        cells = "  ".join(f"{m.split('-')[0]} " + " ".join(f"{a}:{by_item[t['id']][m][a]['marks'] or '·'}" for a in ARMS) for m in MODELS)
        emit("text", f"      {t['label']:<20}{'(control) ' if t['kind'] == 'control' else '':10}{cells}")
    sc = dict(by=by, by_item=by_item)
    emit("score", "", **sc)
    return sc


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_grader(d)
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['team']}'s memory, three ways to sync the edited page")
    world = setup(cli)

    banner(3, TOTAL, f"The first sync ({d['first_sync']}) — meeting notes and the decisions page")
    state = first_sync(cli, world)

    banner(4, TOTAL, f"The decisions page is edited ({d['pages']['decisions']['v2_edited']}) — the sync updates its blocks")
    edited = second_sync(cli, world, state)
    pair = side_by_side(cli, world)
    emit("second_sync", "", rows=edited, pair=pair)

    banner(5, TOTAL, "The same eight questions — ChatGPT and Claude, through the same memory")
    rows = ask_all(cli, world)
    if rows:
        emit("text", "\n  One question, six readers:")
        show_highlights(d, rows)

    banner(6, TOTAL, "The score — per model and per way of writing the edit")
    sc = score(d, rows) if rows else {}

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(reads=rows, score=sc, tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok,  # type: ignore[attr-defined]
                   models=list(MODELS), temperature=TEMPERATURE, runs=RUNS)
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every read and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Re-read the three projects: for each question, the older note and the edited block, side by side."""
    d = load_data()
    world = load_world(cli)
    facts = {a: [fact_text(f) for f in project_facts(cli, world["projects"][ARM_COPY[a]], echo=n == 0)]
             for n, a in enumerate(ARMS)}
    out = {}
    for t in d["topics"]:
        emit("text", f"\n  {t['label']}{'  (control)' if t['kind'] == 'control' else ''}")
        old = next((f for f in facts["A"] if f.startswith(d["pages"][t["page"]]["title"]) and asserted(t["values"], f)), "")
        emit("text", wrap(old, "      older note    │ ", 118))
        for a in ARMS:
            cur = next((f for f in facts[a] if f.startswith(d["pages"]["decisions"]["title"]) and asserted(t["values"], f)), "")
            emit("text", wrap(cur, f"      {a} edited page │ ", 118))
            out.setdefault(t["id"], {})[a] = cur
    emit("memory", "", blocks=out)
    return out


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each arm's memory on both models; graded when it is one of the eight."""
    d = load_data()
    world = load_world(cli)
    t = next((t for t in d["topics"] if t["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = system_prompt(d)
    for m in MODELS:
        emit("text", f"\n  {MODEL_NAME[m]}")
        for n, a in enumerate(ARMS):
            base = baseline(cli, m, question, system, echo=n == 0 and m == MODELS[0])
            r = router(cli, m, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0 and m == MODELS[0])
            if not r:
                die("MemoryRouter is not available on this account (no balance)")
            empty = r["prompt_tokens"] <= base
            g = grade(t, r["reply"]) if t else None
            head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
            emit("text", f"    {ARM_LABEL[a]}: {head}")
            emit("text", wrap(r["reply"], "      | ", 100))
            rows.append(dict(model=m, arm=a, reply=r["reply"], empty=empty, grade=g))
    if t:
        emit("text", f"\n  edited page: {t['current']} · older note: {t['stale'] or '(unchanged)'}")
    emit("asked", "", question=question, item=t and t["id"], rows=rows, current=t and t["current"])
    return rows


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    nfacts = nproj = 0
    for c in COPIES:
        p = find_project(cli, c, echo=not quiet and nproj == 0)
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
        note(f"deleted {len(bs)} boundaries, {nfacts} facts and {nproj} projects" if (bs or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    st = OUT / STATE_FILE
    if st.exists():
        st.unlink()


EXAMPLE = "How many accounts are in the pilot cohort?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = the older note and each edited block, side by side; "
                         "ask \"<question>\" = one question through A, B and C on both models; cleanup = delete everything")
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
        banner(2, 2, "The older note and each edited block, side by side")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question, through A, B and C, on both models")
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

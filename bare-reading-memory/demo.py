#!/usr/bin/env python3
"""bare-reading-memory — a number said on its own at a meeting rarely becomes memory; the gate it decides does.

Quillfold (a fictional company) runs an AI support agent. On 14 September the team set six release gates ("we ship
the new reranker only if recall at 10 on the eval set is at least 0.85"). On 15 October the eval sync read out the six
results, the way people usually do: "Recall at 10 for the new reranker came in at 0.81." MemoryLake's extraction keeps
the gates and usually drops those readings — a bare number with no verdict rarely becomes a fact. Ask the release
assistant whether the reranker ships, and memory can only offer the gate: "Unknown", a guess, or an invented reading.

Three memories of the same two meetings, read through MemoryRouter: A the readings as said · V the same meeting with
each reading said with its verdict ("…, under the 0.85 bar, so the new reranker does not ship") — the decision is
kept, sometimes without the number · H A's extracted facts copied word for word, plus the six readings written by
the app straight from the eval harness (`fact add`, numbers only, no verdict). Six ship questions and two figure questions, three answers each.

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

PREFIX = "mlu-brm"  # memorylake-usecases / bare-reading-memory


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
MODEL = "gpt-4o-mini"
TEMPERATURE = "0.7"   # an assistant's usual setting: the same question gets a slightly different reply each time
RUNS = 3              # answers per question and arm
MAX_TOKENS = 90
COPIES = ("said", "verdict", "harness")   # the three projects
MEETING_COPIES = ("said", "verdict")      # the two that get the meetings (each with its own four people)
COPY_NAME = {"said": "eval sync, readings said as numbers", "verdict": "eval sync, readings said with their verdict",
             "harness": "eval sync + the harness's readings"}
ARMS = ("A", "V", "H")
ARM_LABEL = {"A": "A  readings as said", "V": "V  said with the verdict", "H": "H  + the harness's readings"}
ARM_COPY = {"A": "said", "V": "verdict", "H": "harness"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
MARK = {"ok": "✓", "luck": "◐", "wrong": "✗", "unknown": "?", "made": "✎", "other": "–"}
GRADE_NAME = {"ok": "right, and this memory holds the reading or the decision", "luck": "right word, but this memory holds neither the reading nor the decision",
              "wrong": "wrong", "unknown": "Unknown", "made": "a figure memory does not hold", "other": "no Yes / No / Unknown or figure"}


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


# --------------------------------------------------------------------------- step 2: set up


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


# --------------------------------------------------------------------------- step 3: the meetings

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





# --------------------------------------------------------------------------- step 3: the calls

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
    return json.loads((DATA / "evals.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    """Each meeting copy has its own four people, so a fact that lands on a speaker can be traced to its copy."""
    return [f"{c}-{p['key']}" for c in MEETING_COPIES for p in d["people"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = " " * (len(indent) - 2) + "| " if indent.endswith("| ") else " " * len(indent)
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=sub)


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def at(m: dict) -> datetime:
    return datetime.fromisoformat(m["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def item(d: dict, iid: str) -> dict:
    return next(x for x in d["items"] if x["id"] == iid)


def questions(d: dict) -> list[dict]:
    """The eight questions: six ship decisions (Yes / No) and two figures (the reading itself)."""
    out = [dict(id=x["id"], what=x["what"], ask=x["ask"], kind="decision", of=x["id"], truth=x["truth"]) for x in d["items"]]
    for f in d["figures"]:
        x = item(d, f["of"])
        out.append(dict(id=f["id"], what=f["what"], ask=f["ask"], kind="figure", of=x["id"],
                        truth=f"{x['value']}{x['unit']}".strip()))
    return out


def harness_fact(d: dict, x: dict) -> str:
    """What the app writes for one reading, straight from the eval harness: the number, no verdict."""
    return f"Eval harness run {d['harness']['run']} ({d['harness']['source']}): {x['metric']} = {x['value']}{x['unit']}."


def meeting_lines(d: dict, copy: str) -> dict:
    """The two meetings as a copy hears them: the same gates; the readings said bare ("said") or with their verdict."""
    form = "bare" if copy == "said" else "verdict"
    return {"G": [(x["speaker"], x["gate"]) for x in d["items"]], "E": [(x["speaker"], x[form]) for x in d["items"]]}


TRUTH_WORD = {"Y": "Yes", "N": "No"}
LEAD = re.compile(r"^\W*(yes|no|unknown)\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bnot (?:been )?(?:remembered|recorded|reported|available|mentioned|specified|known|provided)\b|"
                       r"\bno (?:record|information|data|figure|reading|result)s?\b|\bdon'?t have\b|\bdo not have\b|\bnot sure\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")
# numbers that are part of names or dates, not readings: "recall at 10", "p95", "eval set v4", "2,000 sessions", the date
IGNORED_NUMBERS = {"10", "95", "4", "2,000", "2000", "2026", "15", "14", "17", "9"}


def lead_word(reply: str) -> str | None:
    m = LEAD.match(reply or "")
    return m.group(1)[0].upper() if m else None


def cites(x: dict, reply: str) -> bool:
    return bool(re.search(x["value_re"], reply or "", re.I))


def other_figures(x: dict, reply: str) -> list[str]:
    """Numbers in a reply that are neither the reading, the gate's bar, nor part of a name or date."""
    skip = IGNORED_NUMBERS | {x["bar"], x["value"], x["value"].replace(",", "")}
    return [n for n in NUMBER.findall(reply or "") if n.lstrip("$").rstrip(".,") not in skip
            and not re.fullmatch(x["value_re"], n, re.I)]


def grade(q: dict, x: dict, reply: str, held: bool) -> str:
    """One grade per reply.
    decision: ✓ right word and this memory settles it (the reading, or the decision) · ◐ right word but memory holds
              neither — right by luck ("No … recall is not confirmed to be at least 0.85") · ✗ wrong word · ? Unknown · – none.
    figure:   ✓ the reading · ✎ another number (memory does not hold it) · ? says it is not remembered · – neither."""
    if q["kind"] == "decision":
        w = lead_word(reply)
        if not w:
            return "other"
        if w == "U":
            return "unknown"
        if w != q["truth"]:
            return "wrong"
        return "ok" if held else "luck"
    if cites(x, reply):
        return "ok"
    if other_figures(x, reply):
        return "made"
    return "unknown" if NOT_KNOWN.search(reply or "") else "other"


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three: the project's name is context for extraction (a project named for evals
        # keeps more bare readings), so it must not differ between the copies, or say anything about readings.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} team meetings · {ARM_OF[c]}",
                                       "--custom-id", cid(c), "--description", f"{d['org']} team meetings. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects: “said” and “verdict” each get the two meetings (the eval sync said two ways); "
         "“harness” gets what “said” extracted, plus the eval harness's readings")
    n = 0
    for c in MEETING_COPIES:
        for p in d["people"]:
            world["actors"][f"{c}-{p['key']}"] = make_actor(cli, f"{c}-{p['key']}", p["display"], f"{p['role']}. Demo data.",
                                                            echo=n == 0)
            n += 1
    note(f"{len(d['people'])} people, once per meeting copy: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Release assistant ({COPY_NAME[c]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per project — what the release assistant reads through MemoryRouter")
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
    g, e = d["gates_meeting"], d["evals_meeting"]
    emit("text", f"\n    {at(g):%a %d %b %Y}  “{g['name']}” — both copies hear the same six gates:")
    for x in d["items"]:
        emit("text", wrap(f"{person(d, x['speaker'])['display']}: {x['gate']}", "        ", 112))
    emit("text", f"\n    {at(e):%a %d %b %Y}  “{e['name']}” — the six readings, said two ways:")
    for x in d["items"]:
        emit("text", wrap(f"said     {person(d, x['speaker'])['display']}: {x['bare']}", "        ", 112))
        emit("text", wrap(f"verdict  {person(d, x['speaker'])['display']}: {x['verdict']}", "        ", 112))
    emit("text", f"\n    where each gate stands ({today(d):%d %b}):")
    for x in d["items"]:
        emit("text", f"      · {x['what']:<33} {x['value']}{x['unit']:<13} bar {x['bar']:<5} → {TRUTH_WORD[x['truth']]}")
    emit("meetings", "", gates=d["gates_meeting"], evals=d["evals_meeting"],
         items=[dict(id=x["id"], what=x["what"], speaker=x["speaker"], gate=x["gate"], bare=x["bare"], verdict=x["verdict"],
                     value=f"{x['value']}{x['unit']}", bar=x["bar"], truth=TRUTH_WORD[x["truth"]]) for x in d["items"]])


def ingest_copy(cli: CLI, world: dict, copy: str, echo: bool, t0: float) -> list[str]:
    """One copy: the gates meeting, then — once it is extracted — the eval sync. Each line as its speaker, a minute apart."""
    d = load_data()
    lines = meeting_lines(d, copy)
    actors = {p["key"]: world["actors"][f"{copy}-{p['key']}"] for p in d["people"]}
    late = []
    for n, m in enumerate((d["gates_meeting"], d["evals_meeting"])):
        conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", ",".join(actors.values()),
                       "--kind", "GROUP", "--name", f"{d['org']} · {m['name']}", "--custom-id", cid("conv", copy, m["id"]),
                       scoped=True, echo=echo and n == 0)
        for i, (who, line) in enumerate(lines[m["id"]]):
            append(cli, conv["id"], actors[who], f"m{i:02d}", utc(at(m) + timedelta(minutes=i)),
                   f"{label(person(d, who))}: {line}", echo=echo and n == 0 and i == 0)
        if not cooked(cli, conv["id"], echo=echo and n == 0, limit=420):
            late.append(f"{copy}/{m['id']}")
        emit("progress", f"    {copy}: {m['name']} extracted · {int(time.time() - t0)} s", copy=copy, meeting=m["id"])
    return late


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_meetings(d)
    emit("text", "\n    Each meeting is one GROUP conversation, every line as its speaker at the meeting's time; the eval sync goes "
                 "in once the gates meeting is extracted. The two copies run side by side.")
    t0 = time.time()
    with ThreadPoolExecutor(2) as pool:
        late = sum(pool.map(lambda c: ingest_copy(cli, world, c, c == MEETING_COPIES[0], t0), MEETING_COPIES), [])
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — graded as they stand")
    note(f"both copies extracted in {int(time.time() - t0)} s")


# --------------------------------------------------------------------------- step 4: the harness branch

def write_harness(cli: CLI, world: dict) -> dict:
    """H: every fact extracted into "said", copied word for word, plus one fact per reading from the eval harness —
    the number only, no bar and no verdict. A and H differ only by those six facts."""
    d = load_data()
    src = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["said"], echo=True)]
    src = [s for s in src if s]
    p = world["projects"]["harness"]
    if src:
        res = cli.run("fact", "add", *src, "--project", p, scoped=True, echo=True)
        if len((res or {}).get("facts") or []) != len(src):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(src)} texts")
    readings = [harness_fact(d, x) for x in d["items"]]
    cli.run("fact", "add", *readings, "--project", p, scoped=True, echo=True)
    note(f"“harness”: the {len(src)} facts MemoryLake extracted into “said”, copied word for word, then {len(readings)} readings")
    emit("text", "\n    H · “harness” — the readings, written by the app from the eval harness:")
    for r in readings:
        emit("text", wrap(r, "      · ", 116))
    note("numbers only: the app does not decide anything; the model still has to compare each reading with its gate")
    emit("harness", "", copied=len(src), readings=readings, source=d["harness"]["source"])
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return dict(copied=len(src), readings=readings)


# --------------------------------------------------------------------------- step 5: what memory holds

GATE_WORDS = re.compile(r"\bonly if\b|\bat least\b|\bunder\b|\babove\b|\bbelow\b|\bif\b|只有|仅当|如果|低于|高于|至少", re.I)
CONDITIONAL = re.compile(r"\bonly if\b|\bif\b|\bprovided\b|\bas long as\b|\bunless\b|只有|仅当|如果", re.I)
DECISION_WORDS = re.compile(r"\b(?:ships?|shipped|shipping|GA|turned on|turns? on|removed?|removes|stays?|moves?|moved|go(?:es)? ahead)\b"
                            r"|上线|发布|开启|移除|保留", re.I)


def decided(x: dict, fact: str) -> bool:
    """A fact that states how the gate came out ("The new reranker does not ship."), with or without the number: about
    the gate's subject, a decision verb, and not the gate itself restated as a condition."""
    return bool(re.search(x["topic"], fact, re.I) and DECISION_WORDS.search(fact) and not CONDITIONAL.search(fact))


def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per copy and gate: the gate facts, the facts that hold the reading (its number), and — for the meeting copies —
    readings that landed on a speaker instead (MemoryRouter reads the project, not the speakers)."""
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        on_actors = []
        if c in MEETING_COPIES:
            for p in d["people"]:
                a = world["actors"].get(f"{c}-{p['key']}")
                if a:
                    on_actors += [f.get("fact") or "" for f in actor_facts(cli, a)]
        per = {}
        for x in d["items"]:
            about = [f for f in facts if re.search(x["topic"], f, re.I)]
            per[x["id"]] = dict(gate=[f for f in about if GATE_WORDS.search(f) and re.search(re.escape(x["bar"]), f)],
                                reading=[f for f in facts if cites(x, f)],
                                decided=[f for f in facts if decided(x, f)],
                                on_actor=[f for f in on_actors if cites(x, f)])
        out[c] = dict(facts=len(facts), all=facts, actor_facts=on_actors, items=per,
                      readings=sum(1 for x in d["items"] if per[x["id"]]["reading"]),
                      decisions=sum(1 for x in d["items"] if per[x["id"]]["decided"]),
                      gates=sum(1 for x in d["items"] if per[x["id"]]["gate"]))
    return out


def show_census(cen: dict) -> None:
    d = load_data()
    for x in d["items"]:
        emit("text", f"\n    {x['what']} — gate: {x['bar']} · reading {x['value']}{x['unit']} → {TRUTH_WORD[x['truth']]}")
        for c in COPIES:
            r = cen[c]["items"][x["id"]]
            g = "gate ✓" if r["gate"] else "gate –"
            rd = r["reading"][0] if r["reading"] else (r["decided"][0] + "  [the decision, without the number]" if r["decided"]
                                                        else "reading only on a speaker: " + r["on_actor"][0] if r["on_actor"] else "")
            emit("text", wrap(f"{c:<8} {g}  reading {'✓' if r['reading'] else '✗'}  " + (f"“{rd}”" if rd else "(neither the number nor the decision is in memory)"),
                              "      ", 118))
    emit("text", "")
    for c in COPIES:
        oa = sum(1 for x in d["items"] if cen[c]["items"][x["id"]]["on_actor"] and not cen[c]["items"][x["id"]]["reading"])
        emit("text", f"    {c:<8} {cen[c]['facts']:>3} facts · gates {cen[c]['gates']}/{len(d['items'])} · readings "
                     f"{cen[c]['readings']}/{len(d['items'])} · decisions {cen[c]['decisions']}/{len(d['items'])}"
                     + (f" · {oa} more only on a speaker" if oa else "")
                     + ("   (the readings here are the app's six harness facts)" if c == "harness" else ""))
    emit("census", "", **cen)


# --------------------------------------------------------------------------- step 6: ask the release assistant

def system_prompt(d: dict) -> str:
    return d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))


def held(cen: dict, q: dict, arm: str) -> bool:
    """Does the memory this arm reads settle the question? A ship question: the reading, or the decision itself. A
    figure question: only the reading."""
    r = cen[ARM_COPY[arm]]["items"][q["of"]]
    return bool(r["reading"] or (q["kind"] == "decision" and r.get("decided")))


def graded(q: dict, cen: dict | None, reply: str, arm: str) -> dict:
    d = load_data()
    h = held(cen, q, arm) if cen else False
    return dict(grade=grade(q, item(d, q["of"]), reply, h), kind=q["kind"], held=h)


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, cen: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    qs = questions(d)
    jobs = [(q, i) for q in qs for i in range(RUNS)]
    for n, q in enumerate(qs):
        baseline(cli, q["ask"], system, echo=echo_first and n == 0)

    def one(job):
        q, i = job
        r = router(cli, q["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        empty = r["prompt_tokens"] <= BASE[(q["ask"], system)]
        return dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    **graded(q, cen, r["reply"], arm))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {q['what']:<33} {marks:<8} " + (first[:74] + "…" if len(first) > 75 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine,
             truth=TRUTH_WORD.get(q["truth"], q["truth"]))
    return rows


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    dec = [r for r in live if r["kind"] == "decision"]
    fig = [r for r in live if r["kind"] == "figure"]
    return dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
                empty=sum(1 for r in rows if r.get("grade") and r.get("empty")),
                dec_n=len(dec), dec_ok=sum(1 for r in dec if r["grade"] == "ok"), dec_luck=sum(1 for r in dec if r["grade"] == "luck"),
                dec_wrong=sum(1 for r in dec if r["grade"] == "wrong"), dec_unknown=sum(1 for r in dec if r["grade"] == "unknown"),
                dec_other=sum(1 for r in dec if r["grade"] == "other"),
                fig_n=len(fig), fig_ok=sum(1 for r in fig if r["grade"] == "ok"), fig_made=sum(1 for r in fig if r["grade"] == "made"),
                fig_unknown=sum(1 for r in fig if r["grade"] == "unknown"), fig_other=sum(1 for r in fig if r["grade"] == "other"))


def show_example(rows: list[dict]) -> None:
    """Two replies to the same question side by side: A's (memory holds only the gate) and H's (it holds the reading)."""
    d = load_data()
    q = next(q for q in questions(d) if q["id"] == "reranker")
    for a in ("A", "H"):
        r = next((r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("reply") and not r.get("empty")), None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{q['ask']}”  (right answer: {TRUTH_WORD[q['truth']]})")
            emit("text", wrap(r["reply"], f"    {mark(r)} | ", 104))
            emit("example", "", arm=a, row=r, ask=q["ask"], truth=TRUTH_WORD[q["truth"]])


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per arm; ∅ = nothing attached, not graded):")
    emit("text", f"      {'':<34} " + "  ".join(f"{ARM_LABEL[a][:1]:<14}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']} {c['marks']}"  # noqa: E731
        right = TRUTH_WORD.get(q["truth"], q["truth"])
        emit("text", f"      {q['what'] + ' → ' + right:<34} " + "  ".join(f"{cell(cells[a]):<14}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<30}{'ship: right, from memory':>29}{'right word, unsettled':>24}{'Unknown':>9}{'wrong':>7}"
                 f"{'figure right':>14}{'invented':>10}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<30}{x['dec_ok']:>23}/{x['dec_n']:<5}{x['dec_luck']:>18}{'':>6}{x['dec_unknown']:>9}"
                     f"{x['dec_wrong']:>7}{x['fig_ok']:>10}/{x['fig_n']:<3}{x['fig_made']:>10}" + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ right, and this memory holds the reading (or, for a ship question, the decision) · ◐ right word, but this memory "
                 "holds neither (“No … recall is not confirmed to be at least 0.85”) · ✗ wrong · ? Unknown · ✎ a figure memory does not hold · "
                 "– no verdict · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s agent-release memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, "Two meetings: six release gates, then the eval sync — said two ways")
    ingest(cli, world)

    banner(4, TOTAL, "H — the same memory, plus the eval harness's readings")
    hb = write_harness(cli, world)

    banner(5, TOTAL, "What memory holds: the gates, and the numbers that decide them")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the release assistant — three memories, the same questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, cen, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — ship questions and figures, per memory")
    sc = score(cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, harness=hb, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
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
    """Per gate and copy: is the gate in memory, is the reading."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", census=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each memory; graded when it is one of the eight."""
    d = load_data()
    world = load_world(cli)
    q = next((q for q in questions(d) if q["ask"].lower() == question.strip().lower()), None)
    cen = census(cli, world, echo=False) if q else None
    rows = []
    system = system_prompt(d)
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = graded(q, cen, r["reply"], a) if q else {}
        head = "∅ nothing attached" if empty else (f"{MARK[g['grade']]} {GRADE_NAME[g['grade']]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, **g))
    if q:
        x = item(d, q["of"])
        emit("text", f"\n  right answer: {TRUTH_WORD.get(q['truth'], q['truth'])} (reading {x['value']}{x['unit']}, bar {x['bar']})")
    emit("asked", "", question=question, item=q and q["id"], rows=rows, truth=q and TRUTH_WORD.get(q["truth"], q["truth"]))
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
             f"deleted actor's facts could never be removed) and {nproj} projects" if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


EXAMPLE = "Does the new reranker ship?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = per gate: is the gate in memory, is the reading; "
                         "ask \"<question>\" = one question through A, V and H; cleanup = delete everything")
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
        banner(2, 2, "What memory holds: each gate, and the reading that decides it")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the release assistant, through A, V and H")
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

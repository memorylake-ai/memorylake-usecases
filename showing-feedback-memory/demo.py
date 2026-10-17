#!/usr/bin/env python3
"""showing-feedback-memory — feedback texts that don't say which house, pinned on every house.

Wrenfield Homes (a fictional brokerage): Rhea Calloway has three listings on the market — 14 Alder Lane, 22 Birch Court
and 9 Cedar Row. In the first week of October four buyer's agents showed them eight times and texted her their
buyers' feedback afterwards. Two texts name the house; six don't ("Thanks for letting us in today. My buyers felt it
was overpriced for the size …") — the buyer's agent knows which house it was, and so does the showing schedule.

Kept as texted, memory holds the concerns without an address, and the listing assistant, asked what buyers said about
one house, lists every house's concerns: the Alder seller hears that buyers find the place overpriced and noisy.
Three memories, the same four questions: A every text as it arrived · B the same texts with the address the app
looked up in the showing schedule written in front · C one record per showing, written by the app with `fact add` —
plus the same questions with no memory at all, the baseline for what the model says on its own.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 70 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-sfm"  # memorylake-usecases / showing-feedback-memory


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
MAX_TOKENS = 120
COPIES = ("texted", "stamped", "records")   # the three projects
COPY_NAME = {"texted": "every text as it arrived",
             "stamped": "each text with its address stamped in front",
             "records": "one record per showing, written by the app"}
ARMS = ("0", "A", "B", "C")
ARM_LABEL = {"0": "0  no memory", "A": "A  every text as it arrived", "B": "B  address stamped on each text",
             "C": "C  one record per showing"}
ARM_COPY = {"A": "texted", "B": "stamped", "C": "records"}
MARK = {"ok": "✓", "mix": "mix", "wrong": "✗", "unknown": "?"}
GRADE_NAME = {"ok": "right", "mix": "another listing's feedback mixed in", "wrong": "wrong",
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
    return json.loads((DATA / "showings.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [p["key"] for p in d["people"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    # a bullet starts the first line only; a "B | " style gutter repeats on every line
    sub = indent.replace("·", " ") if "·" in indent else indent
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=sub)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def listing(d: dict, key: str) -> dict:
    return next(x for x in d["listings"] if x["id"] == key)


def names_listing(x: dict, text: str) -> bool:
    return bool(re.search(rf"\b(?:{re.escape(x['address'])}|{re.escape(x['short'])})\b", text or "", re.I | re.A))


def message_text(d: dict, t: dict, stamped: str | None = None) -> str:
    p = person(d, t["by"])
    return f"{p['display']} (buyer's agent, {p['brokerage']}): {stamped or t['text']}"


def questions(d: dict) -> list[dict]:
    return d["questions"]


def hm(ts: str) -> str:
    return ts[11:16]


# --------------------------------------------------------------------------- the app's join: text → showing

def resolve(d: dict) -> list[dict]:
    """Each text is matched to the showing it is about: the last showing by the same buyer's agent that ended before
    the text was sent. The schedule knows the house; the text does not have to."""
    out = []
    for t in d["texts"]:
        before = [s for s in d["showings"] if s["by"] == t["by"] and s["end"] <= t["ts"]]
        if not before:
            die(f"text {t['id']} has no showing before it in the schedule")
        s = max(before, key=lambda s: s["end"])
        x = listing(d, s["listing"])
        out.append(dict(text=t["id"], by=t["by"], ts=t["ts"], showing=s["id"], start=s["start"], end=s["end"],
                        listing=x["id"], address=x["address"], named=names_listing(x, t["text"])))
    return out


def stamped_text(d: dict, t: dict, r: dict) -> str:
    """B: the same text, with the address written in front when the text does not already name the house."""
    return t["text"] if r["named"] else f"{r['address']}: {t['text']}"


def record_text(d: dict, t: dict, r: dict) -> str:
    p = person(d, t["by"])
    return (f"Showing feedback for {r['address']} (showing {r['showing']}, {r['start'][:10]} {hm(r['start'])}-{hm(r['end'])} "
            f"UTC, buyer's agent {p['display']} of {p['brokerage']}), texted {r['ts'][:10]} {hm(r['ts'])} UTC: "
            f"\"{t['text']}\"")


# --------------------------------------------------------------------------- grading
# Every rule here has offline cases in selftest(); run `python3 demo.py selftest` after changing one.

CONCERN = {
    "kitchen": r"\bkitchen",
    "yard": r"\b(?:back ?yard|yard|dog)\b",
    "price": r"\b(?:overpric\w*|pric(?:e|ed|es|ey|ing)|expensive|high cost)\b",
    "noise": r"\b(?:nois\w*|traffic|main road|loud)\b",
    "ceiling": r"\bceiling",
    "garage": r"\bgarage",
    "water": r"\b(?:water|stain\w*|leak\w*|damp\w*|moisture)\b",
}
UNKNOWN = (r"\b(?:don't have|do not have|not have (?:the |any |specific )?(?:exact |specific )?(?:number|data|information|feedback)|"
           r"no record|unable to|cannot (?:determine|confirm|say)|can't (?:determine|say)|not specified|not available|"
           r"no information|no (?:specific )?mention|not mentioned|no (?:specific )?(?:feedback|concerns) "
           r"(?:recorded|on record|available|has been))\b|\[(?:insert|specific)")
# A piece that says a concern did not come up, or belongs elsewhere, is not a claim about this house. Narrow on
# purpose: "they could not get past the traffic noise" and "the backyard not being suitable" are claims.
NEG = (r"\b(?:nobody|no one|none|no (?:concerns?|complaints?|issues?|mentions?)|"
       r"not (?:at|about|for|here|this|an? (?:issue|concern|problem)|mentioned|raised|called|said)|"
       r"(?:did|does|was|were)(?: not|n't) (?:mention|raise|say|call|complain|come up|an? (?:issue|concern)))\b")


def answer_body(reply: str) -> str:
    s = " ".join((reply or "").split())
    return re.sub(r"^\**answer:?\**\s*", "", s, flags=re.I)


def clauses(text: str) -> list[str]:
    # list items too: a negation or another house's name only covers its own item
    parts = re.split(r"(?<=[.;:])\s+|,\s*(?:and\s+|but\s+|while\s+|whereas\s+)?|\s+(?:but|while|whereas)\s+|\s*\(|\)",
                     text, flags=re.I)
    return [p for p in parts if p and p.strip()]


def concerns_of(d: dict, key: str) -> list[str]:
    return list(listing(d, key)["concerns"])


def grade(d: dict, q: dict, reply: str) -> str:
    t = answer_body(reply)
    unknown = bool(re.search(UNKNOWN, t, re.I))
    own = listing(d, q["listing"])
    others = [x for x in d["listings"] if x["id"] != own["id"]]
    if q["kind"] == "concerns":
        foreign = [c for x in others for c in x["concerns"]]
        mixed = has_own = False
        for c in clauses(t):
            if any(names_listing(x, c) for x in others) or re.search(NEG, c, re.I):
                continue  # attributed to another house, or said not to apply
            mixed |= any(re.search(CONCERN[k], c, re.I) for k in foreign)
            has_own |= any(re.search(CONCERN[k], c, re.I) for k in own["concerns"])
        if mixed:
            return "mix"
        if has_own:
            return "ok"
        return "unknown" if unknown else "wrong"
    if q["kind"] == "which":
        named = {x["id"] for c in clauses(t) if not re.search(NEG, c, re.I) for x in d["listings"] if names_listing(x, c)}
        if named - {own["id"]}:
            return "mix"
        if own["id"] in named:
            return "ok"
        return "unknown" if unknown else "wrong"
    raise ValueError(q["kind"])


CASES = [
    ("Q1", "Answer: Buyers said the kitchen is dated and needs a full redo, and the backyard is too small for their dog.", "ok"),
    ("Q1", "Answer: Buyers have raised concerns about the need for a garage, low basement ceiling, kitchen requiring a full "
           "redo, small backyard, the home being overpriced, and street noise.", "mix"),
    ("Q1", "Answer: The dated kitchen and the small backyard; the noise complaints were about 22 Birch Court, not this house.", "ok"),
    ("Q1", "Answer: A dated kitchen and a small yard (the garage concern was at Cedar).", "ok"),
    ("Q1", "Answer: I don't have any feedback recorded for 14 Alder Lane.", "unknown"),
    ("Q1", "Answer: Buyers mentioned the kitchen, and they couldn't get past the traffic noise.", "mix"),
    ("Q2", "Answer: Buyers felt 22 Birch Court is overpriced for its size and were bothered by traffic noise.", "ok"),
    ("Q2", "Answer: Buyers have raised concerns about traffic noise being a dealbreaker, despite the nice finishes.", "ok"),
    ("Q2", "Answer: Overpriced for the size, street noise, a dated kitchen and no garage.", "mix"),
    ("Q2", "Answer: Buyers said the price is high compared to what they have seen nearby.", "ok"),
    ("Q3", "Answer: The basement ceiling is too low, there is no garage, and a water stain on the basement wall worried buyers.", "ok"),
    ("Q3", "Answer: Buyers noted the lack of a garage and the low basement ceiling.", "ok"),
    ("Q3", "Answer: Buyers have raised concerns that 9 Cedar Row is overpriced for its size, there is bothersome street "
           "noise from the main road, a garage is needed.", "mix"),
    ("Q3", "Answer: Buyers did not mention noise; their concerns were the low basement ceiling and the missing garage.", "ok"),
    ("Q3", "Answer: Buyers have raised concerns about the outdated kitchen appliances and insufficient natural light.", "mix"),
    ("Q3", "Answer: Buyers liked the layout.", "wrong"),
    ("Q3", "Answer: Buyers have raised concerns that 9 Cedar Row is overpriced for its size, there is bothersome street "
           "noise from the main road, a garage is needed, the basement ceiling is too low, the kitchen requires a full redo, "
           "the backyard is too small for a dog, and they could not get past the traffic noise.", "mix"),
    ("Q1", "Answer: Buyers have raised concerns about the dated kitchen, the need for a full kitchen remodel, and the small "
           "backyard not being suitable for their dog.", "ok"),
    ("Q2", "Answer: Traffic noise and the price; the kitchen was not an issue at this house.", "ok"),
    ("Q4", "Answer: 22 Birch Court is the only listing buyers called overpriced.", "ok"),
    ("Q4", "Answer: Buyers called 22 Birch Court and 14 Alder Lane overpriced.", "mix"),
    ("Q4", "Answer: The buyers felt the following listing was overpriced for the size: [insert listing name or address here].", "unknown"),
    ("Q4", "Answer: The listings that buyers have called overpriced are 123 Maple St, 456 Oak Ave, and 789 Pine Rd.", "wrong"),
    ("Q4", "Answer: Only 22 Birch Court; nobody said 9 Cedar Row was overpriced.", "ok"),
    ("Q4", "Answer: Buyers felt the home was overpriced for the size, but the listing is not specified.", "unknown"),
]


def arm_grade(d: dict, arm: str, q: dict, reply: str) -> str:
    """With no memory there is no feedback to mix up: another house's concern there is invented, so it is ✗, not mix."""
    g = grade(d, q, reply)
    return "wrong" if arm == "0" and g == "mix" else g


def selftest() -> int:
    d = load_data()
    qs = {q["id"]: q for q in questions(d)}
    bad = [(qid, s, want, grade(d, qs[qid], s)) for qid, s, want in CASES if grade(d, qs[qid], s) != want]
    for qid, s, want, got in bad:
        print(f"FAIL {qid} want {want} got {got}: {s}")
    # the join, offline: every text lands on the showing the schedule says, also for an agent who showed two houses
    want = {"t1": "SH-2041", "t2": "SH-2042", "t3": "SH-2047", "t4": "SH-2050", "t5": "SH-2053", "t6": "SH-2056",
            "t7": "SH-2058", "t8": "SH-2061"}
    got = {r["text"]: r["showing"] for r in resolve(d)}
    jbad = [k for k in want if got.get(k) != want[k]]
    for k in jbad:
        print(f"FAIL join {k}: want {want[k]} got {got.get(k)}")
    print(f"grader cases: {len(CASES) - len(bad)}/{len(CASES)} pass · join: {len(want) - len(jbad)}/{len(want)} texts")
    return 1 if bad or jbad else 0


# --------------------------------------------------------------------------- census: what memory holds per house

def census_copy(d: dict, facts: list[str]) -> dict:
    out: dict = {"facts": len(facts), "concerns": {}}
    for x in d["listings"]:
        for k, label in x["concerns"].items():
            hits = [f for f in facts if re.search(CONCERN[k], f, re.I)]
            addressed = [f for f in hits if names_listing(x, f)]
            out["concerns"][f"{x['id']}.{k}"] = dict(listing=x["address"], concern=label, facts=len(hits),
                                                     addressed=len(addressed), shown=(addressed or hits or [""])[0])
    out["no_address"] = [f for f in facts if not any(names_listing(x, f) for x in d["listings"])]
    out["kept"] = sum(1 for v in out["concerns"].values() if v["facts"])
    out["kept_addressed"] = sum(1 for v in out["concerns"].values() if v["addressed"])
    out["total"] = len(out["concerns"])
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
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} listings",
                                       "--custom-id", cid(c), "--description", f"Showing feedback on {d['org']} listings. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name: A gets the texts as sent, B the texts with the address stamped in front, "
         "C the app's showing records")
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"Buyer's agent at {p['brokerage']}. Demo data.",
                                               echo=n == 0)
    note(f"{len(d['people'])} buyer's agents who text feedback: " + ", ".join(f"{p['display']} ({p['brokerage']})" for p in d["people"]))
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Listing assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the listing assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


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


# --------------------------------------------------------------------------- step 3: the texts

def show_texts(d: dict, res: list[dict]) -> None:
    emit("text", f"\n    The showing schedule (what the showing tool knows):")
    for s in d["showings"]:
        emit("text", f"      {s['id']}  {s['start'][:10]} {hm(s['start'])}-{hm(s['end'])}  {listing(d, s['listing'])['address']:<16}"
                     f"  {person(d, s['by'])['display']}")
    emit("text", f"\n    The texts (what Rhea's phone got):")
    emit("text", f"      {'sent':<17}{'from':<15}{'names the house?':<18}text")
    for t, r in zip(d["texts"], res):
        emit("text", f"      {t['ts'][:10]} {hm(t['ts'])}  {person(d, t['by'])['display']:<15}"
                     f"{('yes' if r['named'] else 'no'):<18}{t['text'][:62]}{'…' if len(t['text']) > 62 else ''}")
    emit("texts", "", rows=[dict(t, person=person(d, t["by"])["display"], named=r["named"], showing=r["showing"],
                                 address=r["address"]) for t, r in zip(d["texts"], res)],
         showings=[dict(s, address=listing(d, s["listing"])["address"], person=person(d, s["by"])["display"])
                   for s in d["showings"]])
    n = sum(1 for r in res if not r["named"])
    emit("text", f"\n    {n} of {len(res)} texts don't say which house. The app can tell from the schedule: the last showing by the "
                 "same buyer's agent before the text.")
    for r in res:
        if not r["named"]:
            emit("text", f"      {person(d, r['by'])['display']:<15}text {r['ts'][:10]} {hm(r['ts'])}  →  {r['showing']} "
                         f"({r['start'][:10]} {hm(r['start'])}-{hm(r['end'])})  {r['address']}")
    emit("resolved", "", rows=res)


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


def ingest_one(cli: CLI, world: dict, copy: str, t: dict, body: str, echo: bool, t0: float) -> tuple[str, bool]:
    d = load_data()
    actor = world["actors"][t["by"]]
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", actor, "--kind", "GROUP",
                   "--name", f"Text from {person(d, t['by'])['display']}", "--custom-id", cid("conv", copy, t["id"]),
                   scoped=True, echo=echo)
    mid = append(cli, conv["id"], actor, cid("msg", copy, t["id"]), t["ts"], body, echo=echo)
    ok = wait_read(cli, conv["id"], mid, echo=echo)
    emit("progress", f"    {copy:<8} {t['id']}  {person(d, t['by'])['display']:<15} read · {int(time.time() - t0)} s",
         copy=copy, text_id=t["id"])
    return conv["id"], ok


def ingest(cli: CLI, world: dict, res: list[dict]) -> None:
    d = load_data()
    by_id = {r["text"]: r for r in res}
    jobs = []
    for t in d["texts"]:
        jobs.append(("texted", t, message_text(d, t)))
        jobs.append(("stamped", t, message_text(d, t, stamped_text(d, t, by_id[t["id"]]))))
    emit("text", "\n    Each text is one message in its own GROUP conversation, written by the buyer's agent, stamped with the "
                 "time it was sent.\n    A gets the texts as sent; B the same texts with the address the app looked up "
                 "written in front, e.g.:")
    t2 = next(j for j in jobs if j[0] == "stamped" and not by_id[j[1]["id"]]["named"])
    emit("text", wrap(t2[2], "      B | ", 116))
    t0 = time.time()
    with ThreadPoolExecutor(16) as pool:
        out = list(pool.map(lambda j: ingest_one(cli, world, j[0], j[1], j[2], j is jobs[0], t0), jobs))
    world["convs"] = {f"{j[0]}-{j[1]['id']}": r[0] for j, r in zip(jobs, out)}
    late = [f"{j[0]}/{j[1]['id']}" for j, r in zip(jobs, out) if not r[1]]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — read as they stand")
    note(f"{len(jobs)} conversations, {len(jobs)} messages, all read in {int(time.time() - t0)} s")
    time.sleep(8)


# --------------------------------------------------------------------------- step 4: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(("texted", "stamped")):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        out[copy] = dict(all=facts, **census_copy(d, facts))
    return out


def show_census(d: dict, cs: dict) -> None:
    def cell(v: dict) -> str:
        if not v["facts"]:
            return "NOT IN MEMORY"
        return f"{v['facts']} fact(s), {v['addressed']} name the house" if v["addressed"] else f"{v['facts']} fact(s), no address"
    emit("text", f"\n    {'house':<16}{'concern':<34}{'A  as texted':<32}B  address stamped")
    for x in d["listings"]:
        for k, label in x["concerns"].items():
            a, b = cs["texted"]["concerns"][f"{x['id']}.{k}"], cs["stamped"]["concerns"][f"{x['id']}.{k}"]
            emit("text", f"    {x['address']:<16}{label[:32]:<34}{cell(a):<32}{cell(b)}")
    for copy, a in (("texted", "A"), ("stamped", "B")):
        c = cs[copy]
        emit("text", f"\n    {a}: {c['facts']} facts; {len(c['no_address'])} of them name no house" + (":" if c["no_address"] else ""))
        for f in c["no_address"][:6]:
            emit("text", wrap(f, "      · ", 118))
        if len(c["no_address"]) > 6:
            emit("text", f"      · … {len(c['no_address']) - 6} more")
    for copy, a in (("texted", "A"), ("stamped", "B")):
        c = cs[copy]
        emit("text", f"    {a}: {c['kept']}/{c['total']} concerns are in memory, {c['kept_addressed']}/{c['total']} with the house named")
    emit("census", "", **{k: {kk: vv for kk, vv in v.items()} for k, v in cs.items()})


# --------------------------------------------------------------------------- step 5: C — one record per showing

def write_records(cli: CLI, world: dict, res: list[dict]) -> dict:
    d = load_data()
    by_id = {r["text"]: r for r in res}
    recs = [record_text(d, t, by_id[t["id"]]) for t in d["texts"]]
    cli.run("fact", "add", *recs, "--project", world["projects"]["records"], scoped=True, echo=True)
    note(f"C: {len(recs)} texts → {len(recs)} showing records, each with the house from the schedule and the text verbatim "
         "(`fact add`)")
    for t in recs[1:2]:
        emit("text", wrap(t, "      · ", 118))
    emit("records", "", records=recs)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`
    return dict(records=recs)


# --------------------------------------------------------------------------- step 6: ask

def system_prompt(d: dict) -> str:
    return d["system"].format(org=d["org"], agent=d["agent"], today=today(d).strftime("%A %Y-%m-%d"))


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
        pick = next((r for r in mine if r.get("grade") == "mix"), None) or next((r for r in mine if r.get("reply")), None)
        first = answer_body(pick["reply"]) if pick else ""
        emit("text", f"    {q['what']:<36} {marks:<16} " + (first[:66] + "…" if len(first) > 67 else first))
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
    """One question through every memory: the A reply that lists other houses' concerns, set beside B and C."""
    d = load_data()
    q = next((x for x in questions(d) if any(r["arm"] == "A" and r["item"] == x["id"] and r.get("grade") == "mix" for r in rows)), None)
    if not q:
        note("no answer through A mixed in another house's feedback this run — nothing to set side by side")
        return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "mix")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{q['ask']}”  (truth: {q['truth']})")
            emit("text", wrap(r["reply"], f"    {mark(r):>3} | ", 104))
            emit("example", "", arm=a, row=r, ask=q["ask"], truth=q["truth"])


def score(rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per memory; ∅ = MemoryRouter attached nothing, not graded):")
    emit("text", f"      {'':<46}" + "".join(f"{a:<15}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), mix=sum(1 for r in live if r["grade"] == "mix"),
                            n=len(live), empty=len(mine) - len(live), marks=" ".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']}" + (f" mix {c['mix']}" if c["mix"] else "") + (f" ∅{c['empty']}" if c["empty"] else "")  # noqa: E731
        emit("text", f"      {(q['id'] + ' ' + q['what'])[:45]:<46}" + "".join(f"{cell(cells[a]):<15}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<34}{'right':>9}{'mix':>6}{'✗':>4}{'?':>4}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<34}{x['ok']:>5}/{x['n']:<3}{x['mix']:>6}{x['wrong']:>4}{x['unknown']:>4}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ right · mix another house's feedback in the answer · ✗ wrong · ? says it does not know · "
                 "∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s showing feedback memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, "Eight showings, eight texts — six don't say which house")
    res = resolve(d)
    show_texts(d, res)
    ingest(cli, world, res)

    banner(4, TOTAL, "What memory now holds about each house")
    cs = census(cli, world)
    show_census(d, cs)

    banner(5, TOTAL, "C — the app writes one record per showing")
    rec = write_records(cli, world, res)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the listing assistant — no memory and three memories, the same four questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == "A")
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cs, resolved=res, records=rec, answers=rows, score=sc, tokens=tok,
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
    """Re-read A's and B's facts: which concerns are in memory, and whether the fact names the house."""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census=cs)
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once with no memory and once through each memory; graded when it is one of the four."""
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


EXAMPLE = "What concerns have buyers raised about 14 Alder Lane after showings? List each concern once."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup", "selftest"],
                    help="run = full demo (default); memory = re-read which concerns A's and B's facts hold and whether "
                         "they name the house; ask \"<question>\" = one question with no memory and through A, B and C; "
                         "cleanup = delete everything; selftest = the grader's and the join's offline cases (no account needed)")
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
        banner(2, 2, "What A's and B's facts hold about each house")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the listing assistant: no memory, then A, B and C")
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

#!/usr/bin/env python3
"""client-background-memory — one boundary over the playbook and the client: the client's own words lose.

Tallyfield (a fictional dispatch-software company) onboards new clients with an assistant on MemoryRouter.
Two kinds of memory feed it:
  · the shared onboarding playbook — one project, written by the whole team (the standard steps, and later
    a record of every client who followed them);
  · each client's background — what this client told us, on the client's own actor (Northwind signs in with
    Okta, Copperline sends shipments through the API as JSON and has no file export …).

The obvious wiring is one boundary per client over both: `boundary create --projects <playbook>
--human-actor <client>`. MemoryRouter attaches a few memories per message, ranked by how close they are to
the question. "What file format should Copperline Couriers send for the shipment import?" is worded like the
playbook ("Shipment import: clients send shipments as a CSV file …"), not like the client ("sends shipments
through the Tallyfield REST API as JSON"). So the playbook's CSV notes are attached and the client's line is
not — with a 12-note playbook exactly as with a 293-note one — and the assistant asks for a CSV file the
client cannot produce. The exceptions that survive are the ones that happen to share the playbook's words.

What the run shows:
  · the same 12 questions (3 clients × sign-in / data region / import format / training slot) through each
    client's mixed boundary, with the week-1 playbook (12 notes) and the month-12 playbook (293 notes);
  · what MemoryRouter attached, copied out by Claude: is the client's own line there?
  · the fix the use-case page names — client background loaded before the first message: read the client's
    actor with `fact list --actors` (a handful of facts, all of them) into the system prompt, and let
    MemoryRouter attach only from the playbook. The four questions where the client follows the standard
    path are the control: loading the background must not bend those.

Each reply starts with a line "Answer: …"; its first clause is graded against the client's real answer, and
every answer is printed. Model wording and what MemoryRouter attaches vary run to run; the README reports
totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.  Router
calls are billed to your MemoryLake balance (36 calls on gpt-4o-mini and 16 on claude-haiku-4-5 per run).
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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cbg"  # memorylake-usecases / client-background-memory


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


MODEL = dict(protocol="chat", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Chat Completions)")
CHECKER = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")

ARMS = {
    "week1": dict(label="Week 1 · one boundary", tag="client actor + the week-1 playbook (12 notes)"),
    "month12": dict(label="Month 12 · one boundary", tag="client actor + the month-12 playbook (293 notes)"),
    "first": dict(label="Month 12 · background first", tag="client facts in the system prompt; boundary = playbook only"),
}
ORDER = ["week1", "month12", "first"]

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). If you were given none, write NONE inside the backticks. "
                "Then answer the question.")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "tallyfield.json").read_text(encoding="utf-8"))


def clients_by_key(d: dict) -> dict[str, dict]:
    return {c["key"]: c for c in d["clients"]}


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


def answer_line(reply: str) -> str:
    """The reply's first line 'Answer: …' (markdown bold tolerated); else its first non-empty line."""
    lines = [ln.strip() for ln in (reply or "").splitlines() if ln.strip()]
    for ln in lines:
        m = re.match(r"^[*_#\s]*answer[*_\s]*:[*_\s]*(.*)$", ln, re.I)
        if m:
            return m.group(1).strip(" *_")
    return lines[0] if lines else ""


def first_clause(line: str) -> str:
    """The answer itself, without the reason that follows it: 'XML, as they cannot produce CSV' → 'XML'."""
    line = re.split(r"(?<=[A-Za-z0-9)])\.\s", line, maxsplit=1)[0]
    return re.split(r";|\s—\s|,?\s+\b(?:as they|as their|as it|as that|because|since|which|but|however|so that)\b",
                    line, maxsplit=1, flags=re.I)[0]


def grade(reply: str, q: dict) -> dict:
    """Right = the answer names the client's real answer and not the path that would be wrong for them.
    Only the first clause of the 'Answer:' line counts, so 'XML, as they cannot produce CSV' is XML."""
    line = first_clause(answer_line(reply))
    hit = bool(re.search(q["expect"], line, re.I))
    wrong = bool(re.search(q["forbid"], line, re.I))
    return dict(ok=hit and not wrong, answer=line, standard=wrong and q["exception"])


def is_playbook(line: str, d: dict) -> bool:
    n = norm(line)
    return any(n == norm(t) for pb in d["playbooks"].values() for t in pb["notes"])


def client_fact(line: str, d: dict) -> tuple[str, str] | None:
    """(client key, slot) when a copied-out line is word for word one of the clients' stored facts."""
    n = norm(line)
    for c in d["clients"]:
        for f in c["facts"]:
            if n == norm(f["text"]):
                return c["key"], f["slot"]
    return None


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, pb: str, echo: bool = False):
    return cli.try_run("proj", "get", cid("playbook", pb), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid("client", key), "--by-custom-id", echo=echo)


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


def list_facts(cli: CLI, project: str | None = None, actor: str | None = None, echo: bool = True) -> list[dict]:
    """Every fact in one scope, page by page until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--page-size", "200"]
        args += ["--projects", project] if project else ["--actors", actor]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def leftovers(cli: CLI) -> bool:
    if demo_boundaries(cli) or any(find_project(cli, pb) for pb in ("week1", "month12")):
        return True
    return any(find_actor(cli, c["key"]) for c in load_data()["clients"])


def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left Tallyfield's memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = dict(playbooks={}, clients={})
    for i, (pb, p) in enumerate(d["playbooks"].items()):
        proj = cli.run("proj", "create", "--name", f"Tallyfield onboarding playbook ({p['label']})", "--custom-id",
                       cid("playbook", pb), "--description", "Shared onboarding playbook. Demo data.",
                       scoped=True, echo=i == 0)
        world["playbooks"][pb] = dict(project=proj["id"])
    b = cli.run("boundary", "create", "--name", "Tallyfield playbook only (month 12)", "--custom-id",
                cid("b", "playbook"), "--projects", world["playbooks"]["month12"]["project"], scoped=True)
    world["playbooks"]["month12"]["boundary"] = b["id"]
    note(f"two playbook projects (week 1, month 12) and a playbook-only boundary {b['id']}")
    for i, c in enumerate(d["clients"]):
        first = i == 0
        actor = cli.run("actor", "create", "--custom-id", cid("client", c["key"]), "--display-name", c["name"],
                        "--description", f"Tallyfield client {c['name']}. Demo data.", echo=first)
        try:
            cli.run("actor", "bind", "--actor", actor["id"], scoped=True, echo=first)
        except CLIError as e:
            if not e.has("409", "already"):
                raise
        row = dict(actor=actor["id"])
        for pb in ("week1", "month12"):
            b = cli.run("boundary", "create", "--name", f"{c['name']} onboarding ({pb})", "--custom-id",
                        cid("b", c["key"], pb), "--projects", world["playbooks"][pb]["project"],
                        "--human-actor", actor["id"], scoped=True, echo=first)
            row[pb] = b["id"]
        world["clients"][c["key"]] = row
        if first:
            note("one boundary per client over both kinds of memory: the playbook project + the client's actor; "
                 "the same commands for the other two clients")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    d = load_data()
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    world: dict = dict(playbooks={}, clients={})
    for pb in ("week1", "month12"):
        proj = find_project(cli, pb)
        if not proj:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["playbooks"][pb] = dict(project=proj["id"])
    if cid("b", "playbook") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    world["playbooks"]["month12"]["boundary"] = bs[cid("b", "playbook")]
    for c in d["clients"]:
        a = find_actor(cli, c["key"])
        if not a or any(cid("b", c["key"], pb) not in bs for pb in ("week1", "month12")):
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["clients"][c["key"]] = dict(actor=a["id"], week1=bs[cid("b", c["key"], "week1")],
                                         month12=bs[cid("b", c["key"], "month12")])
    return world


# --------------------------------------------------------------------------- step 3: the memories

def add_facts(cli: CLI, texts: list[str], project: str | None = None, actor: str | None = None,
              echo: bool = True) -> list[str]:
    """`fact add` with many texts in one call; ids come back in the same order. Not idempotent: after a network
    error, add only what is still missing and map texts to ids from the list."""
    scope = ["--project", project] if project else ["--actor", actor]
    if echo:
        shown = ["fact", "add", *scope, *texts[:2]]
        more = f"  … {len(texts) - 2} more" if len(texts) > 2 else ""
        emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace]) + more)
    try:
        res = cli.run("fact", "add", *scope, *texts, scoped=True, echo=False)
        return [f["id"] for f in res["facts"]]
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        have = {f["fact"] for f in list_facts(cli, project, actor, echo=False)}
        missing = [t for t in texts if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", *scope, *missing, scoped=True, echo=False)
        by_text = {f["fact"]: f["id"] for f in list_facts(cli, project, actor, echo=False)}
        return [by_text[t] for t in texts]


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    for i, c in enumerate(d["clients"]):
        a = world["clients"][c["key"]]["actor"]
        add_facts(cli, [f["text"] for f in c["facts"]], actor=a, echo=i == 0)
        n = len(list_facts(cli, actor=a, echo=False))
        if n != len(c["facts"]):
            die(f"{c['name']}'s actor holds {n} facts, expected {len(c['facts'])}")
    note("client background: " + " · ".join(f"{c['name']} {len(c['facts'])}" for c in d["clients"])
         + " fact(s), each on the client's own actor")
    for pb, p in d["playbooks"].items():
        proj = world["playbooks"][pb]["project"]
        notes = p["notes"]
        for k in range(0, len(notes), 40):
            add_facts(cli, notes[k:k + 40], project=proj, echo=k == 0 and pb == "week1")
        facts = list_facts(cli, project=proj, echo=pb == "week1")
        if sorted(f["fact"] for f in facts) != sorted(notes):
            die(f"the {pb} playbook holds {len(facts)} facts, expected {len(notes)}")
        steps = sum(1 for t in notes if not t.startswith(("Onboarding ", "Check-in ")))
        note(f"playbook, {p['label'].lower()}: {len(facts)} notes"
             + (f" ({steps} standard steps + {len(facts) - steps} onboarding and check-in records of clients who followed them)"
                if len(facts) > steps else " (the standard steps, three per topic)"))
    mw = d["playbooks"]["month12"]["notes"]
    emit("text", "\n  Month-12 playbook, a few of its notes:\n"
         + "\n".join(f"    - {t}" for t in (mw[0], mw[15], mw[29], mw[30])))
    emit("memory", "", clients={c["key"]: len(c["facts"]) for c in d["clients"]},
         playbooks={pb: len(p["notes"]) for pb, p in d["playbooks"].items()})


# --------------------------------------------------------------------------- step 4: the background is there

def show_background(cli: CLI, world: dict) -> dict:
    d = load_data()
    out = {}
    for i, c in enumerate(d["clients"]):
        facts = list_facts(cli, actor=world["clients"][c["key"]]["actor"], echo=i == 0)
        out[c["key"]] = [f["fact"] for f in facts]
        emit("text", f"\n  {c['name']} ({c['where']}) — {len(facts)} fact(s) on its actor:\n"
             + "\n".join(f"    - {f['fact']}" for f in facts))
    note("every client exception is stored, on the client's own actor, and each boundary includes that actor")
    emit("background", "", clients=out)
    return out


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, tool: dict = MODEL,
           max_tokens: int = 200, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. `body` holds the system message (and any conversation so far)."""
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
            note("model replies below are skipped; the memory steps still run. Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    secs = time.time() - t0
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    if tool["protocol"] == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct, seconds=round(secs, 1))


def write_body(name: str, system: str, tool: dict = MODEL) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    if tool["protocol"] == "chat":
        body = {"model": tool["model"], "messages": [{"role": "system", "content": system}]}
    else:
        body = {"model": tool["model"], "system": system, "messages": []}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def background_system(d: dict, name: str, facts: list[str]) -> str:
    return (d["system"] + f"\n\nWhat {name} has told us (it applies to every answer about them, and overrides "
            "the standard playbook):\n" + "\n".join(f"- {t}" for t in facts))


def arm_call(cli: CLI, world: dict, arm: str, q: dict, bodies: dict, echo: bool = False) -> dict | None:
    if arm == "first":
        return router(cli, q["say"], bodies[("first", q["client"])], boundary=world["playbooks"]["month12"]["boundary"],
                      echo=echo)
    return router(cli, q["say"], bodies["plain"], boundary=world["clients"][q["client"]][arm], echo=echo)


def answer_row(q: dict, arm: str, r: dict | None) -> dict:
    reply = r["reply"] if r else ""
    g = grade(reply, q) if r else dict(ok=None, answer="", standard=False)
    return dict(client=q["client"], slot=q["slot"], say=q["say"], exception=q["exception"], expected=q["answer"],
                arm=arm, reply=reply, **g)


def show_row(d: dict, q: dict, got: list[dict], head: bool = True) -> None:
    names = clients_by_key(d)
    out = [f"\n    {names[q['client']]['name']}: {q['say']}"
           + ("" if q["exception"] else "   (standard path)")] if head else []
    for row in got:
        tail = "   ← the standard path" if row["standard"] else (f"   ← expected: {q['answer']}" if row["ok"] is False else "")
        out.append(f"      {row['arm']:<8} {mark(row['ok'])} {textwrap.shorten(row['answer'] or row['reply'], 70)}{tail}")
    emit("text", "\n".join(out))


def ask_mixed(cli: CLI, world: dict) -> list[dict]:
    """Every question through the client's two mixed boundaries (week-1 playbook, month-12 playbook).
    One question at a time, its two arms in parallel, each result shown as it comes in."""
    d = load_data()
    bodies = {"plain": write_body("plain", d["system"])}
    rows = []
    for n, q in enumerate(d["questions"]):
        if n == 0:
            first = arm_call(cli, world, "week1", q, bodies, echo=True)
            note("every other call is the same command with that client's boundary")
            got = [answer_row(q, "week1", first), answer_row(q, "month12", arm_call(cli, world, "month12", q, bodies))]
        else:
            with ThreadPoolExecutor(2) as pool:
                rs = list(pool.map(lambda a: arm_call(cli, world, a, q, bodies), ("week1", "month12")))
            got = [answer_row(q, a, r) for a, r in zip(("week1", "month12"), rs)]
        rows += got
        for row in got:
            emit("answer", "", **row)
        show_row(d, q, got)
    if any(r["ok"] is not None for r in rows):
        exc = [r for r in rows if r["exception"]]
        emit("text", "\n    right: " + " · ".join(
            f"{a} {sum(1 for r in rows if r['arm'] == a and r['ok'])}/{len(d['questions'])}" for a in ("week1", "month12"))
            + "\n    on the 8 client exceptions: " + " · ".join(
            f"{a} {sum(1 for r in exc if r['arm'] == a and r['ok'])}/8" for a in ("week1", "month12"))
            + "\n    answered with the standard path instead: " + " · ".join(
            f"{a} {sum(1 for r in exc if r['arm'] == a and r['standard'])}" for a in ("week1", "month12")))
    return rows


# --------------------------------------------------------------------------- step 6: what MemoryRouter attached

def attached(cli: CLI, boundary: str, say: str) -> list[str] | None:
    """Ask Claude the same question through the same boundary, told (in the system prompt) to copy out first every
    memory MemoryRouter attached. The user message is unchanged, so retrieval sees the same query."""
    r = router(cli, say, OUT / "body-checker.json", boundary=boundary, tool=CHECKER, max_tokens=1500, echo=False)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    return [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]


def inspect_all(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    names = clients_by_key(d)
    if not cli.router_ok:  # type: ignore[attr-defined]
        note("skipped: needs MemoryRouter")
        return []
    write_body("checker", d["system"] + " " + CHECK_SYSTEM, tool=CHECKER)
    checks = []
    for q in [q for q in d["questions"] if q["exception"]]:
        with ThreadPoolExecutor(2) as pool:
            got = list(pool.map(lambda a: (a, attached(cli, world["clients"][q["client"]][a], q["say"])),
                                ("week1", "month12")))
        out = [f"\n    {names[q['client']]['name']}: {q['say']}"]
        for arm, lines in got:
            if lines is None:
                continue
            kinds = [client_fact(ln, d) for ln in lines]
            own = any(k == (q["client"], q["slot"]) for k in kinds)
            pb = sum(1 for ln in lines if is_playbook(ln, d))
            other = len(lines) - pb - sum(1 for k in kinds if k)
            row = dict(client=q["client"], slot=q["slot"], arm=arm, lines=lines, own=own, playbook=pb,
                       client_lines=sum(1 for k in kinds if k), unmatched=other)
            checks.append(row)
            what = (f"{len(lines)} attached: {pb} playbook, {row['client_lines']} {names[q['client']]['name']}"
                    + (f", {other} not stored" if other else ""))
            out.append(f"      {arm:<8} {'✓' if own else '✗'} the client's line   ({what})")
            if not own:
                pb_lines = [ln for ln in lines if is_playbook(ln, d)][:2]
                out += [f"                 [playbook] {textwrap.shorten(ln, 74)}" for ln in pb_lines]
            emit("inspect", "", **row)
        emit("text", "\n".join(out))
    if checks:
        per = {a: [c for c in checks if c["arm"] == a] for a in ("week1", "month12")}
        note("the client's own line attached: " + " · ".join(
            f"{a} {sum(c['own'] for c in per[a])}/{len(per[a])}" for a in per)
             + "; playbook lines per question: " + " · ".join(
            f"{a} {sum(c['playbook'] for c in per[a]) / max(1, len(per[a])):.1f}" for a in per))
        note("the checker is its own call, so what it was given can differ a little from what the answer above got; "
             "over many runs the pattern holds (README, Measured)")
    return checks


# --------------------------------------------------------------------------- step 7: background first

def background_first(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    bodies = {}
    for i, c in enumerate(d["clients"]):
        facts = [f["fact"] for f in list_facts(cli, actor=world["clients"][c["key"]]["actor"], echo=i == 0)]
        bodies[("first", c["key"])] = write_body(f"first-{c['key']}", background_system(d, c["name"], facts))
        if i == 0:
            note(f"all {len(facts)} of {c['name']}'s facts go into the system prompt (out/body-first-{c['key']}.json); "
                 "MemoryRouter attaches from the playbook only")
    rows = []
    for n, q in enumerate(d["questions"]):
        r = arm_call(cli, world, "first", q, bodies, echo=n == 0)
        row = answer_row(q, "first", r)
        rows.append(row)
        emit("answer", "", **row)
        show_row(d, q, [row])
    if any(r["ok"] is not None for r in rows):
        exc = [r for r in rows if r["exception"]]
        std = [r for r in rows if not r["exception"]]
        emit("text", f"\n    right: {sum(1 for r in rows if r['ok'])}/{len(rows)}"
             f" · client exceptions {sum(1 for r in exc if r['ok'])}/{len(exc)}"
             f" · standard-path questions {sum(1 for r in std if r['ok'])}/{len(std)} (the control)")
    return rows


# --------------------------------------------------------------------------- scoreboard

def scoreboard(rows: list[dict], checks: list[dict]) -> dict:
    d = load_data()
    names = clients_by_key(d)
    w = [12, 12, 12]
    lines = [f"\n    {'':<34}" + "".join(f"{h:>{n}}" for h, n in zip(("week 1", "month 12", "bg first"), w))]
    cell = {(r["client"], r["slot"], r["arm"]): r for r in rows}
    for q in d["questions"]:
        lab = f"{names[q['client']]['name'].split()[0]}: {d['slots'][q['slot']]['label']}" + ("" if q["exception"] else " (std)")
        lines.append(f"    {lab:<34}" + "".join(f"{mark(cell[(q['client'], q['slot'], a)]['ok']):>{n}}"
                                               for a, n in zip(ORDER, w)))
    tot = {a: sum(1 for r in rows if r["arm"] == a and r["ok"]) for a in ORDER}
    exc = {a: sum(1 for r in rows if r["arm"] == a and r["ok"] and r["exception"]) for a in ORDER}
    std = {a: sum(1 for r in rows if r["arm"] == a and r["standard"]) for a in ORDER}
    n, ne = len(d["questions"]), sum(1 for q in d["questions"] if q["exception"])
    lines.append(f"    {'right':<34}" + "".join(f"{str(tot[a]) + '/' + str(n):>{k}}" for a, k in zip(ORDER, w)))
    lines.append(f"    {'client exceptions honoured':<34}" + "".join(f"{str(exc[a]) + '/' + str(ne):>{k}}" for a, k in zip(ORDER, w)))
    lines.append(f"    {'told the standard path instead':<34}" + "".join(f"{std[a]:>{k}}" for a, k in zip(ORDER, w)))
    own = {}
    if checks:
        own = {a: sum(c["own"] for c in checks if c["arm"] == a) for a in ("week1", "month12")}
        lines.append(f"    {'client line attached (checker)':<34}"
                     + "".join(f"{str(own[a]) + '/' + str(ne):>{k}}" for a, k in zip(("week1", "month12"), w))
                     + f"{'in system':>{w[2]}}")
    emit("text", "\n".join(lines))
    emit("scoreboard", "", totals=tot, exceptions=exc, standard=std, attached=own, questions=n, exception_questions=ne)
    return dict(right=tot, exceptions=exc, standard=std, attached=own)


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — two playbook projects, three client actors, one boundary per client and playbook")
    world = setup(cli)

    banner(3, TOTAL, "What Tallyfield knows — the shared playbook, and each client's background")
    store(cli, world)

    banner(4, TOTAL, "Each client's background is stored, on the client's actor")
    bg = show_background(cli, world)
    time.sleep(8)  # new facts reach retrieval a few seconds after `fact list`

    banner(5, TOTAL, f"{d['today']} — 12 onboarding questions through each client's boundary (12-note vs 293-note playbook)")
    note("`router chat --boundary` (gpt-4o-mini, temperature 0); each reply starts with 'Answer: …', graded against "
         "the client's real answer")
    rows = ask_mixed(cli, world)

    banner(6, TOTAL, "Why — what MemoryRouter attached (Claude copies out what sits above the question)")
    note("the 8 exception questions again, through claude-haiku-4-5, told in the system prompt to copy out its memory first")
    checks = inspect_all(cli, world)

    banner(7, TOTAL, "Background first — the client's facts before the first message, MemoryRouter for the playbook")
    rows += background_first(cli, world)

    banner(8, TOTAL, "Scoreboard")
    tot = scoreboard(rows, checks)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(answers=rows, checks=checks, background=bg, totals=tot, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every question, answer and attached line: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def pick_client(d: dict, key: str) -> dict:
    names = clients_by_key(d)
    k = key.lower()
    match = [c for c in d["clients"] if c["key"] == k or c["name"].lower().startswith(k)]
    if not match:
        die(f"unknown client {key!r}; one of: {', '.join(names)}")
    return match[0]


def ask_one(cli: CLI, client: str, say: str, arms: list[str]) -> list[dict]:
    """One question about a client through the chosen arms, as the memory stands now."""
    d = load_data()
    c = pick_client(d, client)
    world = load_world(cli)
    # a free-form question may say "they": every wiring is told which client the manager means
    bodies = {"plain": write_body(f"ask-{c['key']}", d["system"] + f" The client: {c['name']}.")}
    if "first" in arms:
        facts = [f["fact"] for f in list_facts(cli, actor=world["clients"][c["key"]]["actor"])]
        bodies[("first", c["key"])] = write_body(f"first-{c['key']}", background_system(d, c["name"], facts))
    q = dict(client=c["key"], say=say)
    rows = []
    for i, arm in enumerate(arms):
        r = arm_call(cli, world, arm, q, bodies, echo=True)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        emit("text", f"    {ARMS[arm]['label']}:\n      " + "\n      ".join(textwrap.wrap(r["reply"], 92)))
        rows.append(dict(arm=arm, reply=r["reply"]))
    return rows


def show_client(cli: CLI, client: str) -> dict:
    """What MemoryLake holds about one client, and the boundaries that read it."""
    d = load_data()
    c = pick_client(d, client)
    world = load_world(cli)
    w = world["clients"][c["key"]]
    facts = list_facts(cli, actor=w["actor"])
    emit("text", f"\n  {c['name']} — actor {w['actor']}, {len(facts)} fact(s):\n"
         + "\n".join(f"    - {f['fact']}" for f in facts))
    for pb in ("week1", "month12"):
        n = len(list_facts(cli, project=world["playbooks"][pb]["project"], echo=False))
        emit("text", f"  boundary {w[pb]}: this actor + the {d['playbooks'][pb]['label'].lower()} playbook ({n} notes)")
    emit("text", f"  background first: these {len(facts)} fact(s) in the system prompt + boundary "
         f"{world['playbooks']['month12']['boundary']} (playbook only)")
    return dict(facts=[f["fact"] for f in facts])


# --------------------------------------------------------------------------- cleanup

def delete_scoped(cli: CLI, fact_id: str, scope: list[str]) -> None:
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

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    try:
        found = list_facts(cli, actor=actor_id, echo=False)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):
            try:
                found = list_facts(cli, actor=actor_id, echo=False)
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        delete_scoped(cli, f["id"], ["--actor", actor_id])
    left = list_facts(cli, actor=actor_id, echo=False)
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)




def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    if bs and not quiet:
        note(f"deleted {len(bs)} boundaries")
    for pb in ("week1", "month12"):
        proj = find_project(cli, pb, echo=not quiet and pb == "week1")
        if not proj:
            continue
        facts = list_facts(cli, project=proj["id"], echo=False)
        left = facts
        for attempt in range(4):  # a list right after many deletes can still show a few: delete those and look again
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_scoped(cli, f["id"], ["--project", proj["id"]]), left))
            time.sleep(2 * attempt)
            left = list_facts(cli, project=proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in the {pb} playbook after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=False)
        if not quiet:
            note(f"deleted the {pb} playbook project and its {len(facts)} fact(s)")
    n_actors = n_facts = 0
    for c in load_data()["clients"]:
        a = find_actor(cli, c["key"], echo=not quiet and n_actors == 0)
        if a:
            n_facts += delete_actor(cli, a["id"])
            n_actors += 1
    if n_actors and not quiet:
        note(f"deleted {n_actors} client actors and the {n_facts} fact(s) on them (facts first: "
             "`actor delete` alone would leave them undeletable)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "client", "cleanup"],
                    help="run = full demo (default); ask <client> \"<question>\" = one question through each wiring "
                         "as the memory stands now; client <client> = what MemoryLake holds about a client; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: <client> \"<question>\"; for client: <client>")
    ap.add_argument("--arm", choices=ORDER, action="append", help="for ask: only this wiring (repeatable)")
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
    if args.command == "client":
        if len(args.args) != 1:
            die("usage: python3 demo.py client <client>")
        banner(2, 2, f"What MemoryLake holds about {args.args[0]}")
        show_client(cli, args.args[0])
        return
    if args.command == "ask":
        if len(args.args) != 2:
            die('usage: python3 demo.py ask <client> "<question>" [--arm week1|month12|first]')
        banner(2, 2, "One question about a client, through each wiring, as the memory stands now")
        ask_one(cli, args.args[0], args.args[1], args.arm or ORDER)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py client northwind` or '
          '`python3 demo.py ask northwind "Which identity provider does their IT team manage?"`; '
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

#!/usr/bin/env python3
"""client-alias-memory — an account manager asks about "Project Kestrel", and the assistant answers for another client.

Copperline Studio (a fictional creative agency) keeps one brief per client in shared memory: six clients, each with
an approved tagline, a contact, a launch date and a banned word. An account assistant (an ordinary model call through
MemoryRouter with `--boundary`) answers "what is the approved tagline for Brightwater Foods?" from them, and gets it
right.

But nobody at the agency says "Brightwater Foods". They say "Project Kestrel" — an internal codename that appears in
no brief. Asked about Project Kestrel, MemoryRouter attaches some other client's brief and the model answers with
that client's tagline, under the name it was asked about. Nothing in the reply says the brief is for someone else.

The demo measures three ways to teach the assistant the agency's aliases, all from one alias registry kept as facts
in MemoryLake:
  · the registry in the system prompt (read with `fact list`) — the model now knows Kestrel is Brightwater Foods,
    but retrieval runs on the question as typed, so the right brief still isn't attached: it says "not specified" or
    makes a tagline up;
  · the app rewrites the alias to the client's name before the call (`fact list --query "Project Kestrel"`) — the
    right brief is attached, except when MemoryRouter attaches nothing at all, and then the model invents a tagline;
  · rewritten, and the app only shows an answer when something was attached (prompt tokens above the same call
    without a boundary), retrying up to 3 times.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 110 calls per run, roughly 30,000 tokens).
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

PREFIX = "mlu-cam"  # memorylake-usecases / client-alias-memory


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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat. So are uploads:
    # the demo always uploads with --on-conflict overwrite, so a second attempt replaces the same file.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance",
                 "upload"}
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
        # A multi-line argument (the system prompt with the alias registry) is shown as its first line; it is printed in full
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



MODEL = "gpt-4o-mini"        # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 3                     # answers per question per arm
CHECKS = 2                   # checker calls per question
TRIES = 3                    # the attach check: calls before the app gives up on a question
PROJECTS = ("briefs", "aliases")                     # MemoryLake projects the demo creates
ARMS = ("alias", "registry", "rewrite", "checked")   # the four ways of asking


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "agency.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(key: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{key}.md"


def brief_path(key: str) -> Path:
    return DATA / "briefs" / f"{key}.md"


def alias_fact(c: dict) -> str:
    return f'Internal alias: "{c["alias"]}" is our name for the client {c["name"]}.'


ALIAS_FACT = re.compile(r'Internal alias: "(?P<alias>[^"]+)" is our name for the client (?P<client>.+?)\.?$')


def parse_alias(fact: str) -> tuple[str, str] | None:
    m = ALIAS_FACT.match(fact.strip())
    return (m.group("alias"), m.group("client")) if m else None


def registry_block(d: dict, facts: list[str]) -> str:
    """The alias registry as the app puts it in the system prompt: the facts it read from memory, one per line."""
    return "\n".join([d["registry_header"], *[f"- {f}" for f in facts], d["registry_footer"]])


def system_for(d: dict, arm: str, registry: list[str] | None) -> str:
    return d["system"] + ("\n\n" + registry_block(d, registry or []) if arm == "registry" else "")


def alias_in(text: str, aliases: list[str]) -> str | None:
    """The registry alias the question uses (longest first, whole words, any case)."""
    for a in sorted(aliases, key=len, reverse=True):
        if re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", text, re.I):
            return a
    return None


def swap(text: str, alias: str, client: str) -> str:
    return re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", client, text, count=1, flags=re.I)


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"')
                    .lower().split())


def clients_view(d: dict) -> list[dict]:
    return [dict(key=c["key"], name=c["name"], alias=c["alias"], kind=c["kind"], product=c["product"],
                 tagline=c["tagline"], contact=c["contact"], launch=c["launch"], banned=c["banned"],
                 file=f"{c['key']}.md") for c in d["clients"]]


def client(d: dict, key: str) -> dict:
    return next(c for c in d["clients"] if c["key"] == key)


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
# The reply says the briefs do not give a tagline for this client.
DECLINE = re.compile(r"no (?:approved )?tagline|no (?:record|information|brief|data|details|entry|mention)\b|"
                     r"not (?:been )?(?:available|found|mentioned|recorded|included|specified|provided|documented|"
                     r"listed|given|stated)|"
                     r"(?:don't|do not|doesn't|does not) (?:have|appear|contain|include|mention|specify|exist)|"
                     r"there (?:is|are|was) no\b|unable to|cannot (?:find|provide|determine|confirm)|"
                     r"can't (?:find|provide)", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def tagline_hits(d: dict, text: str) -> list[str]:
    """The clients whose approved tagline appears in the text."""
    t = norm(text)
    return [c["key"] for c in d["clients"] if norm(c["tagline"]) in t]


def grade(d: dict, q: dict, reply: str, tokens: int, empty: int) -> dict:
    """Every reply is graded, the ∅ ones too: with nothing attached it is still what the account manager reads.
      right = the client's own tagline · borrowed = another client's tagline (an answer for someone else) ·
      declined = says the briefs give none · invented = a tagline from nowhere"""
    whole = " ".join(reply.split())
    nothing = bool(tokens and empty and tokens <= empty)
    hits = tagline_hits(d, whole)
    if q["client"] in hits:
        v = "right"
    elif hits:
        v = "borrowed"
    elif DECLINE.search(answer_line(reply)) or DECLINE.search(whole):
        v = "declined"
    else:
        v = "invented"
    return dict(empty=nothing, clause=answer_line(reply), hits=hits, verdict=v)


MARK = {"right": "✓", "borrowed": "⇄", "declined": "–", "invented": "✎", "held": "◌", None: "·"}
ORDER = ("borrowed", "invented", "declined", "held", "right", None)
VERDICTS = ("right", "borrowed", "declined", "invented", "held")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") and a["verdict"] != "held" else "")


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, name: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(name), "--by-custom-id", scoped=True, echo=echo)


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


def demo_files(cli: CLI, echo: bool = False) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        items += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            break
    return [i for i in items if (i.get("name") or "").startswith(PREFIX + "-")]


PROJECT_NAME = {"briefs": "client briefs", "aliases": "client alias registry"}


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, p) for p in PROJECTS) or demo_files(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for name in PROJECTS:
        p = cli.run("proj", "create", "--name", f"{d['agency']} {PROJECT_NAME[name]}", "--custom-id", cid(name),
                    "--description", f"{PROJECT_NAME[name].capitalize()}. Demo data.", scoped=True)
        world["project"][name] = p["id"]
    b = cli.run("boundary", "create", "--name", "Account assistant: client briefs", "--custom-id", cid("briefs"),
                "--projects", world["project"]["briefs"], scoped=True)
    world["boundary"]["briefs"] = b["id"]
    note("two projects: the six client briefs (documents), and the alias registry — what the account managers call "
         "each client (facts). One boundary, over the briefs. Every answer is `router chat … --boundary`, and "
         "MemoryRouter picks what to attach")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    world: dict = dict(project={}, boundary={})
    for name in PROJECTS:
        p = find_project(cli, name)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["project"][name] = p["id"]
    if cid("briefs") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    world["boundary"]["briefs"] = bs[cid("briefs")]
    return world


# --------------------------------------------------------------------------- step 3: the briefs and the registry

def project_docs(cli: CLI, project: str) -> list[dict]:
    page_ = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True, echo=False) or {}
    return page_.get("items") or []


def wait_docs(cli: CLI, project: str, names: set[str]) -> list[dict]:
    for attempt in range(72):
        docs = project_docs(cli, project)
        bad = [x for x in docs if x.get("status") == "error" and x.get("name") in names]
        if bad:
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        if names <= {x.get("name") for x in docs if x.get("status") == "okay"}:
            return docs
        time.sleep(5)
    die("the documents are still processing after 6 minutes; run again later")


def upload(cli: CLI, key: str, path: Path, echo: bool = False) -> str:
    return cli.run("lib", "upload", os.path.relpath(path), "--name", lib_name(key), "--on-conflict", "overwrite",
                   echo=echo)["item_id"]


def import_into(cli: CLI, project: str, items: list[str], names: set[str], echo: bool = False) -> None:
    try:
        cli.run("proj", "doc", "import", "--project", project, *items, "--wait", scoped=True, echo=echo)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, names)


def fact_text(x: dict) -> str:
    return x.get("fact") or x.get("text") or ""


def registry_facts(cli: CLI, project: str, query: str | None = None, echo: bool = False) -> list[dict]:
    args = ["fact", "list", "--projects", project, "--page-size", "200"] + (["--query", query] if query else [])
    return (cli.run(*args, scoped=True, echo=echo) or {}).get("items") or []


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for c in d["clients"]:
        text = brief_path(c["key"]).read_text(encoding="utf-8")
        if f"Client brief - {c['name']}" not in text or any(ord(ch) > 127 for ch in text) or c["alias"] in text or any(
                v not in text for v in (c["tagline"], c["contact"], c["launch"], c["banned"])):
            die(f"data/briefs/{c['key']}.md does not match agency.json (or is not plain ASCII, or names the alias)")
    view = clients_view(d)
    out = [f"\n  {d['agency']} — {len(view)} client briefs, one per client (each names the client, never the alias):"]
    for v in view:
        out.append(f"    {v['name']:<25} {v['product']:<25} {v['file']}")
        out.append(f"        approved tagline “{v['tagline']}” · contact {v['contact']} · launch {v['launch']}")
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {c["key"]: upload(cli, c["key"], brief_path(c["key"]), echo=k == 0) for k, c in enumerate(d["clients"])}
    note(f"{len(items)} briefs uploaded to the Library and imported into the briefs project")
    import_into(cli, world["project"]["briefs"], list(items.values()), {lib_name(c["key"]) for c in d["clients"]}, True)
    note(f"indexed in {int(time.time() - t0)} s")
    emit("text", "\n  The alias registry — what the account managers call each client, one fact each, stored verbatim:")
    added = cli.run("fact", "add", "--project", world["project"]["aliases"], *[alias_fact(c) for c in d["clients"]],
                    scoped=True) or {}
    got = added.get("facts") or []
    for f in got:
        emit("text", f"    · {f.get('fact')}")
    if len(got) != len(d["clients"]):
        die(f"expected {len(d['clients'])} alias facts, the server stored {len(got)}")
    emit("memory", "", clients=view, facts=[f.get("fact") for f in got], items=len(items))
    return dict(items=items)


# --------------------------------------------------------------------------- step 4: who is "Project Kestrel"?

def index_files(cli: CLI, project: str, query: str, echo: bool = False) -> list[str]:
    """The documents `search --types document` returns for the query, in rank order."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "3",
                  scoped=True, echo=echo) or {}
    return [(doc.get("document_name") or "")[len(PREFIX) + 1:] for doc in res.get("documents") or []]


def resolve(cli: CLI, world: dict, alias: str, echo: bool = False) -> str | None:
    """The client name the registry gives for this alias: `fact list --query <alias>`, then the fact's own wording."""
    for x in registry_facts(cli, world["project"]["aliases"], alias, echo=echo):
        got = parse_alias(fact_text(x))
        if got and got[0].lower() == alias.lower():
            return got[1]
    return None


def lookup(cli: CLI, world: dict) -> dict:
    """For every alias in the questions: what the registry says it means (exact), and where `search` ranks the right
    brief for the question as typed."""
    d = load_data()
    rows = []
    for k, q in enumerate(d["questions"]):
        name = resolve(cli, world, q["alias"], echo=k == 0)
        files = index_files(cli, world["project"]["briefs"], q["say"], echo=k == 0)
        want = f"{q['client']}.md"
        rank = files.index(want) + 1 if want in files else None
        rows.append(dict(q=q["id"], alias=q["alias"], kind=q["kind"], client=name, search=files, rank=rank))
        emit("text", f"  {q['alias']:<24} registry → {name or 'not in the registry':<25}  search top 3: "
                     + (", ".join(files) or "nothing") + (f"   (its brief: #{rank})" if rank else "   (its brief: not in top 3)"))
    note("the registry lookup is exact: one fact per alias, naming the client. Search ranks by similarity to the "
         "question as typed; the alias is in no brief, so where the right brief lands is luck")
    emit("lookup", "", rows=rows)
    return dict(rows=rows)


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


def chat(cli: CLI, say: str, boundary: str | None, system: str, echo: bool = False) -> tuple[str, int] | None:
    args = ["chat", say, "--model", MODEL, "--max-tokens", "120", "--temperature", "0", "--system", system]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI, says: dict, system: str) -> dict:
    """Each question once without a boundary, with the same system prompt: those prompt tokens are the system prompt
    and the question alone. A boundary call with the same count had nothing attached (∅)."""
    keys = list(says)
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda kq: chat(cli, says[kq[1]], None, system, kq[0] == 0), enumerate(keys)))
    return {k: (g[1] if g else 0) for k, g in zip(keys, got)}


def answer(cli: CLI, q: dict, say: str, boundary: str, system: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, say, boundary, system, echo)
    if not got:
        return dict(q=q["id"], say=say, reply="", prompt_tokens=0, verdict=None, empty=False, clause="", hits=[])
    text, tokens = got
    return dict(q=q["id"], say=say, reply=text, prompt_tokens=tokens, **grade(load_data(), q, text, tokens, empty))


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- step 5: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.md)\)")


def read_attached(d: dict, text: str, tokens: int, empty: int = 0) -> dict:
    """What the checker copied out: each attached entry's file label (a brief, named by its client). With no more
    prompt tokens than the same call without a boundary, nothing was attached (∅)."""
    nothing = bool(tokens and empty and tokens <= empty)
    parts = text.split("```")
    block = parts[1] if len(parts) >= 3 else text
    if nothing or block.strip() == "NONE" or len(parts) < 3:
        after = " ".join((parts[-1] if len(parts) >= 3 else "").split())
        return dict(readable=False, declined=not nothing and block.strip() == "NONE", nothing=nothing, entries=[],
                    said=after[:220], raw=text[:4000], tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    names = {c["key"]: c["name"] for c in d["clients"]}
    entries = []
    seen = set()
    for m in marks:
        key = m.group(1)[len(PREFIX) + 1:-3]
        if key not in seen:
            seen.add(key)
            entries.append(dict(file=f"{key}.md", key=key if key in names else None, client=names.get(key)))
    if not entries and block.strip():
        # Copied without the file labels: attribute by the brief heading in the text.
        for c in d["clients"]:
            if f"Client brief - {c['name']}" in block:
                entries.append(dict(file=None, key=c["key"], client=c["name"]))
        if not entries:
            entries.append(dict(file=None, key=None, client=None))
    return dict(readable=True, declined=False, nothing=False, entries=entries, said="", raw=text[:4000], tokens=tokens)


def check(cli: CLI, q: dict, boundary: str | None, empty: int = 0, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer calls were given. Without a boundary, its prompt tokens are the baseline."""
    d = load_data()
    args = ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
            "--system", d["checker"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return dict(readable=False, declined=False, nothing=False, entries=[], said="", raw="", tokens=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(d, text, u.get("input_tokens", 0), empty)


def entry_line(e: dict, q: dict) -> str:
    tag = "  ← the right client" if e["key"] == q["client"] else ("  ← another client" if e["key"] else "")
    if not e["file"]:
        return "(copied without its file label)" + (f" — the brief for {e['client']}{tag}" if e["client"] else "")
    return f"({e['file']}) — the brief for {e['client']}{tag}" if e["client"] else f"({e['file']})"


def check_line(c: dict, q: dict) -> str:
    if c["readable"]:
        return ", ".join(entry_line(e, q) for e in c["entries"]) or "(no entry)"
    if c["nothing"]:
        return "nothing attached (∅)"
    return f"wrote NONE: “{c['said'][:150]}”" if c["said"] else "wrote NONE"


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    note(f"for each alias, {CHECK_MODEL} is asked {CHECKS}× through the briefs boundary to copy out everything above "
         "the question (its own call: the attachment can differ from the answer calls'), and once without a boundary "
         "for its ∅ baseline")
    with ThreadPoolExecutor(4) as pool:
        base = list(pool.map(lambda q: check(cli, q, None)["tokens"], qs))
    first = check(cli, qs[0], world["boundary"]["briefs"], base[0], echo=True)
    jobs = [(n, k) for n in range(len(qs)) for k in range(CHECKS) if (n, k) != (0, 0)]
    with ThreadPoolExecutor(6) as pool:
        got = dict(zip(jobs, pool.map(lambda nk: check(cli, qs[nk[0]], world["boundary"]["briefs"], base[nk[0]]), jobs)))
    got[(0, 0)] = first
    out, raws = [], []
    for n, q in enumerate(qs):
        copies = [got[(n, k)] for k in range(CHECKS)]
        emit("text", f"\n  {q['id']}  {q['say']}   ({q['alias']} = {client(d, q['client'])['name']})\n"
             + "\n".join(f"      {k + 1}. {check_line(c, q)}" for k, c in enumerate(copies)))
        out.append(dict(q=q["id"], alias=q["alias"], kind=q["kind"], client=q["client"],
                        copies=[{k_: c[k_] for k_ in ("readable", "declined", "nothing", "entries", "said", "tokens")}
                                for c in copies]))
        raws.append(dict(q=q["id"], raw=[c["raw"] for c in copies]))
    copies = [(c["client"], x) for c in out for x in c["copies"]]
    n_empty = sum(x["nothing"] for _, x in copies)
    n_right = sum(any(e["key"] == k for e in x["entries"]) for k, x in copies)
    n_other = sum(bool(x["entries"]) and not any(e["key"] == k for e in x["entries"]) for k, x in copies)
    note(f"{len(copies)} copies: the right client's brief attached in {n_right}; only another client's brief in "
         f"{n_other}; nothing attached in {n_empty}")
    emit("attached", "", checks=out, empty=n_empty, right=n_right, other=n_other, copies=len(copies))
    OUT.mkdir(exist_ok=True)
    (OUT / "checker.json").write_text(json.dumps(raws, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- steps 6–9: ask

def rewritten(cli: CLI, world: dict, q: dict, aliases: list[str], echo: bool = False) -> tuple[str, str | None]:
    """The question with the alias swapped for the client's name, as the registry gives it."""
    alias = alias_in(q["say"], aliases)
    name = resolve(cli, world, alias, echo=echo) if alias else None
    return (swap(q["say"], alias, name) if alias and name else q["say"]), name


def checked(cli: CLI, world: dict, q: dict, say: str, base: int, echo: bool = False) -> dict:
    """Rewritten, then the app's own check after the call: prompt tokens no higher than the same call without a
    boundary mean MemoryRouter attached nothing, and the reply is not shown. Up to TRIES calls."""
    d = load_data()
    tries = []
    for k in range(TRIES):
        a = answer(cli, q, say, world["boundary"]["briefs"], d["system"], base, echo=echo and k == 0)
        if a["verdict"] is None:
            return a
        tries.append(a["prompt_tokens"] - base)
        if not a["empty"]:
            a.update(tries=tries)
            return a
    reply = (f"Answer: (not shown) MemoryRouter attached no brief in {TRIES} tries, so the app does not show the "
             f"model's reply. Ask again, or open the brief.")
    return dict(q=q["id"], say=say, reply=reply, prompt_tokens=base, verdict="held", empty=True,
                clause=answer_line(reply), hits=[], tries=tries)


def ask_all(cli: CLI, world: dict, arm: str, system: str, base: dict, aliases: list[str]) -> list[dict]:
    d = load_data()
    boundary = world["boundary"]["briefs"]
    qs = d["questions"]
    says = {q["id"]: q["say"] for q in qs}
    if arm in ("rewrite", "checked"):
        for n, q in enumerate(qs):
            says[q["id"]] = rewritten(cli, world, q, aliases, echo=n == 0 and arm == "rewrite")[0]
    if arm == "checked":
        ask = lambda q, echo=False: checked(cli, world, q, says[q["id"]], base.get(q["id"], 0), echo)
    else:
        ask = lambda q, echo=False: answer(cli, q, says[q["id"]], boundary, system, base.get(q["id"], 0), echo)
    first = ask(qs[0], True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(ask, q) for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        asked = says[q["id"]]
        emit("text", f"\n  {q['id']}  {asked}" + (f"   (typed: “{q['say']}”)" if asked != q["say"] else "")
             + f"\n      truth: {q['truth']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        extra = ""
        if arm == "checked":
            retried = sum(len(a.get("tries") or []) > 1 for a in answers)
            extra = f"   [retried {retried}×]" if retried else ""
        emit("text", f"      answers {' '.join(shown(a) for a in answers)}{extra}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], kind=q["kind"], arm=arm, say=asked, answers=answers))
        emit("asked", "", q=q["id"], arm=arm, alias=q["alias"], akind=q["kind"], say=asked, typed=q["say"],
             truth=q["truth"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], tokens=a["prompt_tokens"],
                           hits=a["hits"], tries=a.get("tries")) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
    out["answers"] = len(ans)
    out["empty"] = sum(bool(a["empty"]) for a in ans if a["verdict"] != "held")
    out["empty_invented"] = sum(bool(a["empty"]) and a["verdict"] == "invented" for a in ans)
    out["retried"] = sum(len(a.get("tries") or []) > 1 for a in ans)
    return out


def tally_line(t: dict) -> str:
    return (f"{t['right']}/{t['answers']} right · {t['borrowed']} another client's tagline · {t['invented']} invented"
            + (f" ({t['empty_invented']} with nothing attached)" if t["empty_invented"] else "")
            + f" · {t['declined']} “not specified”"
            + (f" · {t['held']} held back (nothing attached in {TRIES} tries)" if t["held"] else "")
            + (f" · {t['retried']} needed a retry" if t["retried"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the account manager instead of the right tagline: one line per distinct reply."""
    order = ["borrowed", "invented", "declined"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text[:150] in seen:
                continue
            seen.add(text[:150])
            found.append((order.index(a["verdict"]), f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:150]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 10
LOCK = threading.Lock()
ARM_STEP = {"alias": 6, "registry": 7, "rewrite": 8, "checked": 9}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — the client briefs, and the alias registry")
    world = setup(cli)

    banner(3, TOTAL, "The agency's memory — six client briefs, and what the account managers call each client")
    store(cli, world)

    banner(4, TOTAL, "Who is “Project Kestrel”? — the registry lookup vs search")
    looked = lookup(cli, world)

    banner(5, TOTAL, "What MemoryRouter hands the model — asked by alias")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies: dict = {}
    base: dict = {}
    registry: list[str] = []
    aliases: list[str] = []
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the account assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        if arm != "alias" and not registry:
            facts = registry_facts(cli, world["project"]["aliases"], echo=True)
            registry = sorted(fact_text(x) for x in facts)
            aliases = [p[0] for p in (parse_alias(f) for f in registry) if p]
        if arm == "registry":
            emit("text", "\n  system prompt, as the app sends it on every call:\n"
                 + "\n".join("    │ " + x for x in system_for(d, arm, registry).splitlines()))
        if arm == "rewrite":
            note("before the call the app finds the registry alias in the question, runs `fact list --query <alias>` "
                 "on the registry and swaps in the client's name. The system prompt is the plain one from step 6")
        if arm == "checked":
            note(f"the same rewritten question. After each call the app compares prompt tokens with the same call "
                 f"without a boundary: equal means MemoryRouter attached nothing, so the reply is not shown and the "
                 f"call is repeated — up to {TRIES} calls, then the app says it has no brief to answer from")
        system = system_for(d, arm, registry)
        key = {"alias": "alias", "registry": "registry"}.get(arm, "rewrite")
        if key not in base:
            note("first, each question once without a boundary and with this system prompt: those prompt tokens are "
                 "the prompt alone, so a boundary call with the same count had nothing attached (∅)")
            says = {q["id"]: q["say"] for q in d["questions"]}
            if key == "rewrite":
                says = {q["id"]: swap(q["say"], q["alias"], client(d, q["client"])["name"]) for q in d["questions"]}
            base[key] = baselines(cli, says, system)
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ⇄ another "
             "client's tagline · ✎ invented · – “not specified” · ◌ held back by the app · ∅ nothing attached")
        got = ask_all(cli, world, arm, system, base[key], aliases)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(10, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<26} {tally_line(tallies[arm])}")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the account manager instead of the right tagline (⇄ another client's "
                     "· ✎ invented · – not specified · ∅ nothing attached):\n"
             + "\n".join(said[:12]) + (f"\n    … {len(said) - 12} more in out/report.json" if len(said) > 12 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, lookup=looked, attached=attached, baselines=base, registry=registry,
                   tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:12])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: the six client briefs and the alias registry facts."""
    d = load_data()
    view = clients_view(d)
    for v in view:
        emit("text", f"\n  {v['name']} — {v['product']} — {v['file']}   (the account managers say “{v['alias']}”)\n"
                     f"      approved tagline “{v['tagline']}” · contact {v['contact']} · launch {v['launch']} · "
                     f"never “{v['banned']}”")
    emit("text", "\n  the alias registry as facts:\n" + "\n".join(f"    · {alias_fact(c)}" for c in d["clients"]))
    out = dict(clients=view, facts=[alias_fact(c) for c in d["clients"]])
    emit("agency", "", **out)
    return out


def show_alias(cli: CLI, text: str) -> dict:
    """One alias: the client the registry gives for it (exact), and what search returns for it."""
    world = load_world(cli)
    alias = " ".join(text.split())
    if not alias:
        die("give an alias, e.g. \"Project Kestrel\"")
    name = resolve(cli, world, alias, echo=True)
    files = index_files(cli, world["project"]["briefs"], f"What is the approved tagline for {alias}?", echo=True)
    if name:
        emit("text", f"\n  “{alias}” is in the registry: {name}. The app asks about {name}, not about “{alias}”.")
    else:
        emit("text", f"\n  “{alias}” is not in the registry (`fact list --query` found no fact for it). The app can say "
                     "it does not know the client instead of letting retrieval guess.")
    emit("text", f"  search for “What is the approved tagline for {alias}?” returns: " + (", ".join(files) or "nothing"))
    out = dict(alias=alias, client=name, search=files)
    emit("alias", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = n_proj = n_facts = 0
    for name in PROJECTS:
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        for k, f in enumerate(registry_facts(cli, proj["id"], echo=False)):
            cli.run("fact", "delete", f["id"], "--project", proj["id"], scoped=True, echo=not quiet and k == 0)
            n_facts += 1
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundary, {n_facts} alias fact(s), {n_proj} project(s) ({n_docs} document(s)) and "
             f"{len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "alias", "briefs", "cleanup"],
                    help="run = full demo (default); alias <name> = which client the registry gives for it, and what "
                         "search returns; briefs = the six briefs and the alias registry (offline); "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for alias: an alias, e.g. \"Project Kestrel\"")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "briefs":
        banner(1, 1, "Briefs — the six client briefs and the alias registry (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "alias":
        if not args.args:
            die("usage: python3 demo.py alias \"Project Kestrel\"")
        banner(2, 2, f"Alias — “{' '.join(args.args)}”: which client? what does search return?")
        show_alias(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py alias \"Project Kestrel\"`; `python3 demo.py cleanup` removes the demo data.")


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

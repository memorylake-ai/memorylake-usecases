#!/usr/bin/env python3
"""consent-withdrawal-memory — two participants withdraw consent; their notes are deleted; the assistant fills the gap.

Tallyfox (a fictional invoicing app) runs customer interviews, and its UX research team keeps the notes in shared
memory so a research assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "what did
Dana Reyes say about the onboarding checklist?". Two of the eight participants withdraw consent, and the team does
what it should: deletes their notes everywhere, and checks that the index holds none of their words.

Then it asks the assistant about them. MemoryRouter attaches by topic, so a question about Dana brings back other
people's notes on the onboarding checklist, and the model puts their words in her mouth; when nothing is attached at
all, it answers on its own — "Yes, Dana Reyes provided valuable insights … can be quoted". The deletion is complete;
what is missing is the record that it happened. A consent register (who withdrew and when, do not quote — none of what
they said) and a notice under each deleted file's own name are the two records this demo measures. Neither helps
with a name nobody ever interviewed: the model gives Omar Haddad someone else's words in every arm.

The demo shows:
  · the eight interviews, imported into three projects;
  · the withdrawal: `proj doc delete` in all three projects, `lib delete`, then `search` for their words (0 left);
  · what each project keeps in their place: nothing / the consent register / a notice per person;
  · what MemoryRouter hands the model for a question about someone with no notes (a checker copies it out);
  · eight questions (four about the two who withdrew, one about a name never interviewed, three controls) through
    the three boundaries, each asked several times.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (89 calls per run, roughly 30,000 tokens).
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

PREFIX = "mlu-cwm"  # memorylake-usecases / consent-withdrawal-memory


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
RUNS = 3                     # answers per question per boundary
ARMS = ("deleted", "register", "tombstone")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "study.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{file}.md"


REGISTER = "consent-register"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def withdrawn(d: dict) -> list[dict]:
    return [p for p in d["participants"] if p.get("withdrawn")]


def by_id(d: dict) -> dict:
    return {p["id"]: p for p in d["participants"]}


def register_text(d: dict) -> str:
    """The team's consent register: who withdrew and when, and that nothing they said may be used. No interview
    content: it is the record of a deletion, not a copy of what was deleted."""
    lines = [f"# Consent register - {d['study']}", "",
             f"Kept by {d['lead']} (research lead). One line per participant who withdrew consent.", ""]
    for p in withdrawn(d):
        lines.append(f"- {p['id']} {p['name']} ({p['org']}): consent withdrawn on {p['withdrawn']}. Interview notes and "
                     f"recording deleted on {p['deleted']}. Do not quote, summarise or attribute anything to this participant.")
    rest = ", ".join(p["id"] for p in d["participants"] if not p.get("withdrawn"))
    lines += ["", f"All other participants ({rest}) consented to quotes in internal readouts."]
    return "\n".join(lines) + "\n"


def tombstone_text(d: dict, p: dict) -> str:
    """A notice under the deleted file's own name: withdrawn, deleted, do not quote."""
    return (f"# Interview notes - {p['id']} {p['name']} - CONSENT WITHDRAWN\n\n"
            f"Participant: {p['id']} {p['name']}, {p['org']}\nStudy: {d['study']}\n\n"
            f"{p['id']} {p['name']} withdrew consent on {p['withdrawn']}. The interview notes and the recording were "
            f"deleted on {p['deleted']}.\nNothing this participant said may be quoted, summarised or attributed to them, "
            "in any readout or deck.\n")


def left_files(d: dict, arm: str) -> list[tuple[str, str]]:
    """(file, text) of what each arm leaves behind after the deletion."""
    if arm == "register":
        return [(REGISTER, register_text(d))]
    if arm == "tombstone":
        return [(p["file"], tombstone_text(d, p)) for p in withdrawn(d)]
    return []


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
# The reply says it cannot answer: consent withdrawn, notes deleted, nothing on record.
DECLINE = re.compile(r"withdr[ae]w|withdrawn|consent|\bdeleted\b|cannot (?:provide|share|quote|be quoted|answer|find)|"
                     r"can't (?:provide|share|quote|find)|unable to|no (?:notes?|records?|information|interview|mention|data|"
                     r"(?:specific |available )?(?:comments?|details|feedback|insights?))\b|not (?:available|found|mentioned|"
                     r"recorded|listed|included|in (?:the|our) (?:notes|study|interviews?))|(?:don't|do not|doesn't|does not) "
                     r"(?:have|appear|contain|include|mention)|(?:wasn't|was not|isn't|is not) (?:a )?(?:participant|interviewed|"
                     r"part of)|there (?:are|is) no\b|none of the", re.I)
NO = re.compile(r"^\W*no\b|cannot|can't|can ?not|should(?:n't| not)|not (?:be )?(?:quoted|allowed|permitted|able)|"
                r"withdr[ae]w|withdrawn|\bdeleted\b", re.I)
YES = re.compile(r"^\W*yes\b", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def phrase_hits(d: dict, text: str) -> dict:
    """Participant id → the phrases from their notes that appear in the text (whole words)."""
    t, out = norm(text), {}
    for p in d["participants"]:
        got = [ph for ph in p["phrases"] if re.search(rf"(?<![\w-]){re.escape(ph)}(?![\w-])", t)]
        if got:
            out[p["id"]] = got
    return out


def grade(d: dict, q: dict, reply: str, tokens: int, empty: int) -> dict:
    """Every reply is graded, the ∅ ones too: with nothing attached it is still what the researcher reads.
      content (withdrawn or unlisted person): right = says it cannot answer (withdrawn, deleted, no notes) ·
        borrowed = another participant's words put on them · leaked = the deleted notes' own words · invented = other
      quote: right = no · used = yes · other = neither
      control / list: right = the fact (both names, no withdrawn name) · leaked = a withdrawn participant named · other"""
    whole = " ".join(reply.split())
    a = answer_line(reply)
    nothing = bool(tokens and empty and tokens <= empty)
    hits = phrase_hits(d, whole)
    own = {q["about"]} if isinstance(q["about"], str) else set(q["about"])
    gone = {p["id"] for p in withdrawn(d)}
    out = dict(empty=nothing, clause=a, phrases=hits)
    if q["kind"] == "quote":
        v = "used" if YES.search(a) else "right" if NO.search(a) else "other"
    elif q["kind"] == "content":
        if DECLINE.search(a) and not (set(hits) - own):
            v = "right"
        elif set(hits) & own & gone:
            v = "leaked"
        elif set(hits) - own:
            v = "borrowed"
        else:
            v = "invented"
    elif q["kind"] == "list":
        names = [p for p in d["participants"] if p["name"].split()[0].lower() in norm(whole)]
        if any(p["id"] in gone for p in names):
            v = "leaked"
        else:
            v = "right" if all(norm(o) in norm(whole) for o in q["ok"]) else "other"
    else:
        v = "right" if any(o in norm(whole) for o in q["ok"]) else "other"
    out["verdict"] = v
    return out


MARK = {"right": "✓", "borrowed": "⇄", "invented": "✎", "used": "✗", "leaked": "!", "other": "·", None: "–"}
ORDER = ("leaked", "used", "borrowed", "invented", "other", "right", None)
VERDICTS = ("right", "borrowed", "invented", "used", "leaked", "other")
GROUPS = ("withdrawn", "unlisted", "control")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "")


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


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, a) for a in ARMS) or demo_files(cli):
        note("an earlier run left the study behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['study']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Interview notes: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Research assistant: {d['arms'][arm]['short']}",
                    "--custom-id", cid(arm), "--projects", p["id"], scoped=True)
        world["project"][arm], world["boundary"][arm] = p["id"], b["id"]
    note("three projects, a boundary over each. Every answer is `router chat … --boundary` and MemoryRouter picks "
         "what to attach")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p or cid(arm) not in bs:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["project"][arm], world["boundary"][arm] = p["id"], bs[cid(arm)]
    return world


# --------------------------------------------------------------------------- step 3: the interview notes

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


def upload(cli: CLI, file: str, path: Path, echo: bool = False) -> str:
    return cli.run("lib", "upload", os.path.relpath(path), "--name", lib_name(file), "--on-conflict", "overwrite",
                   echo=echo)["item_id"]


def import_into(cli: CLI, project: str, items: list[str], names: set[str], echo: bool = False) -> None:
    try:
        cli.run("proj", "doc", "import", "--project", project, *items, "--wait", scoped=True, echo=echo)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, names)


def notes_view(d: dict) -> list[dict]:
    out = []
    for p in d["participants"]:
        text = (DATA / "notes" / f"{p['file']}.md").read_text(encoding="utf-8")
        bullets = [l_[2:] for l_ in text.splitlines() if l_.startswith("- ")]
        out.append(dict(id=p["id"], name=p["name"], org=p["org"], file=f"{p['file']}.md", withdrawn=p.get("withdrawn"),
                        deleted=p.get("deleted"), bullets=bullets, words=len(text.split())))
    return out


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for p in d["participants"]:
        text = (DATA / "notes" / f"{p['file']}.md").read_text(encoding="utf-8")
        if p["name"] not in text or any(ord(ch) > 127 for ch in text):
            die(f"data/notes/{p['file']}.md does not match study.json (or is not plain ASCII)")
    view = notes_view(d)
    out = [f"\n  {d['study']} — {len(view)} interviews, notes kept by {d['lead']}:"]
    for v in view:
        out.append(f"    {v['id']}  {v['name']:<16} {v['org']:<27} {v['file']}")
        out.append(f"         “{v['bullets'][0][:104]}”")
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {}
    for k, p in enumerate(d["participants"]):
        items[p["file"]] = upload(cli, p["file"], DATA / "notes" / f"{p['file']}.md", echo=k == 0)
    note(f"{len(items)} uploads to the Library; each of the three projects imports all {len(items)}")
    names = {lib_name(p["file"]) for p in d["participants"]}
    with ThreadPoolExecutor(len(ARMS)) as pool:
        for fut in [pool.submit(import_into, cli, world["project"][arm], list(items.values()), names, arm == ARMS[0])
                    for arm in ARMS]:
            fut.result()
    note(f"all three projects indexed in {int(time.time() - t0)} s")
    emit("memory", "", notes=view, items=len(items))
    return dict(items=items)


# --------------------------------------------------------------------------- step 4: the withdrawal

def index_text(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every document chunk `search --types document` returns for the project."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "10",
                  scoped=True, echo=echo) or {}
    out = []
    for doc in res.get("documents") or []:
        for it in doc.get("items") or []:
            text = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            if text:
                out.append(dict(file=doc.get("document_name") or "", text=text))
    return out


def withdrawn_in_index(cli: CLI, d: dict, project: str, echo: bool = False) -> dict:
    """What the index still holds of the withdrawn participants: their own phrases, and any chunk naming them."""
    gone = withdrawn(d)
    found: dict = {p["id"]: dict(phrases=set(), files=set(), named=set()) for p in gone}
    for p in gone:
        query = f"{p['name']} {p['org']} " + " ".join(p["phrases"][:3])
        for c in index_text(cli, project, query, echo and p is gone[0]):
            hits = phrase_hits(d, c["text"]).get(p["id"], [])
            f = found[p["id"]]
            f["phrases"].update(hits)
            if hits:
                f["files"].add(c["file"])
            if p["name"].lower() in c["text"].lower():
                f["named"].add(c["file"])
    return {k: {kk: sorted(vv) for kk, vv in v.items()} for k, v in found.items()}


def withdraw(cli: CLI, world: dict, stored: dict) -> dict:
    d = load_data()
    gone = withdrawn(d)
    for p in gone:
        emit("text", f"\n  {p['id']} {p['name']} ({p['org']}) withdrew consent on {p['withdrawn']}: "
                     f"delete {p['file']}.md everywhere")
    names = {lib_name(p["file"]) for p in gone}
    for k, arm in enumerate(ARMS):
        docs = [x for x in project_docs(cli, world["project"][arm]) if x.get("name") in names]
        if len(docs) != len(gone):
            die(f"{arm}: expected {len(gone)} withdrawn documents in the project, found {len(docs)}")
        cli.run("proj", "doc", "delete", "--project", world["project"][arm], *[x["id"] for x in docs], scoped=True,
                echo=k == 0)
    for k, p in enumerate(gone):
        cli.run("lib", "delete", stored["items"][p["file"]], echo=k == 0)
    note("the two documents deleted from all three projects (indexed text and derived memory with them) and the "
         "originals deleted from the Library")
    t0 = time.time()
    for attempt in range(12):
        with ThreadPoolExecutor(len(ARMS)) as pool:
            left = dict(zip(ARMS, pool.map(lambda a: withdrawn_in_index(cli, d, world["project"][a],
                                                                         a == ARMS[0] and attempt == 0), ARMS)))
        if not any(v["phrases"] or v["named"] for arm in ARMS for v in left[arm].values()):
            break
        time.sleep(5)
    docs = {arm: sorted(x.get("name", "")[len(PREFIX) + 1:] for x in project_docs(cli, world["project"][arm])) for arm in ARMS}
    for arm in ARMS:
        bits = [f"{p['name']}: " + (", ".join(left[arm][p["id"]]["phrases"]) or "0 phrases") +
                (f" · named in {', '.join(left[arm][p['id']]['named'])}" if left[arm][p["id"]]["named"] else "")
                for p in gone]
        emit("text", f"  {d['arms'][arm]['short']:<21} {len(docs[arm])} documents · their words left in the index — "
             + "; ".join(bits))
    clean = not any(v["phrases"] or v["named"] for arm in ARMS for v in left[arm].values())
    note(("search over all three projects finds none of their words and no chunk naming them"
          if clean else "the index still returns some of their text (see above)") + f" ({int(time.time() - t0)} s after the delete)")
    emit("withdrawn", "", left=left, docs=docs, clean=clean, people=[dict(id=p["id"], name=p["name"], org=p["org"],
                                                                        withdrawn=p["withdrawn"], file=p["file"]) for p in gone])
    return dict(left=left, clean=clean)


# --------------------------------------------------------------------------- step 5: what is left behind

def leave_behind(cli: CLI, world: dict) -> dict:
    d = load_data()
    (OUT / "left").mkdir(parents=True, exist_ok=True)
    shown_: dict = {}
    for arm in ARMS:
        files = left_files(d, arm)
        if not files:
            emit("text", f"\n  {d['arms'][arm]['short']}: nothing — the notes are gone and so is any trace of them")
            shown_[arm] = []
            continue
        items = []
        for k, (file, text) in enumerate(files):
            path = OUT / "left" / f"{file}.md"
            path.write_text(text, encoding="utf-8")
            items.append(upload(cli, file, path, echo=k == 0))
        emit("text", f"\n  {d['arms'][arm]['short']}: " + ", ".join(f"{f}.md" for f, _ in files) + "\n"
             + "\n".join("    │ " + line_ for f, t in files[:1] for line_ in t.strip().splitlines() if line_.strip()))
        import_into(cli, world["project"][arm], items, {lib_name(f) for f, _ in files}, echo=True)
        shown_[arm] = [dict(file=f"{f}.md", text=t) for f, t in files]
    note("the register names who withdrew and when, and holds none of what they said; each notice sits under the "
         "deleted file's own name, so MemoryRouter labels it like the notes it replaces")
    emit("left", "", arms=shown_)
    return shown_


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


def chat(cli: CLI, q: dict, boundary: str | None, echo: bool = False) -> tuple[str, int] | None:
    d = load_data()
    args = ["chat", q["say"], "--model", MODEL, "--max-tokens", "150", "--temperature", "0", "--system", d["system"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI) -> dict:
    """Each question once without a boundary: its prompt tokens are the system prompt and the question alone. A
    boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda kq: chat(cli, kq[1], None, kq[0] == 0), enumerate(d["questions"])))
    return {q["id"]: (g[1] if g else 0) for q, g in zip(d["questions"], got)}


def answer(cli: CLI, q: dict, boundary: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, q, boundary, echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, empty=False, clause="", phrases={})
    text, tokens = got
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(load_data(), q, text, tokens, empty))


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- step 6: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.md)\)")


def read_attached(d: dict, text: str, tokens: int, empty: int = 0) -> dict:
    """What the checker copied out: each attached entry's file label, and whose words are in it. With no more prompt
    tokens than the same call without a boundary, nothing was attached (∅). When it writes NONE anyway it often says,
    in its own words, whose notes it was given: those names are kept as `named`."""
    nothing = bool(tokens and empty and tokens <= empty)
    parts = text.split("```")
    block = parts[1] if len(parts) >= 3 else text
    if nothing or block.strip() == "NONE" or len(parts) < 3:
        after = " ".join((parts[-1] if len(parts) >= 3 else "").split())
        named = [p["id"] for p in d["participants"] if p["name"] in after or p["name"].split()[0] + " " in after]
        return dict(readable=False, declined=not nothing and block.strip() == "NONE", nothing=nothing, entries=[],
                    named=named, said=after[:220], raw=text[:4000], tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    spans = [(m.group(1)[len(PREFIX) + 1:], m.end(), marks[k + 1].start() if k + 1 < len(marks) else len(block))
             for k, m in enumerate(marks)]
    if not spans and block.strip():
        spans = [(None, 0, len(block))]
    files = {f"{p['file']}.md": p["id"] for p in d["participants"]}
    entries = []
    for file, start, end in spans:
        body = block[start:end]
        whose = sorted(phrase_hits(d, body))
        entries.append(dict(file=file, participant=files.get(file or ""), whose=whose,
                            register=REGISTER in (file or "") or "Consent register" in body,
                            notice="CONSENT WITHDRAWN" in body))
    return dict(readable=True, declined=False, nothing=False, entries=entries, named=[], said="", raw=text[:4000],
                tokens=tokens)


def check(cli: CLI, q: dict, boundary: str | None, empty: int = 0, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer calls were given. Without a boundary, its prompt tokens are the baseline."""
    d = load_data()
    args = ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
            "--system", d["checker"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return dict(readable=False, declined=False, nothing=False, entries=[], named=[], said="", raw="", tokens=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(d, text, u.get("input_tokens", 0), empty)


def entry_line(d: dict, e: dict) -> str:
    names = by_id(d)
    if not e["file"]:
        return "(copied without its file label)"
    who = names[e["participant"]]["name"] if e["participant"] else ""
    tag = " — the consent register" if e["register"] else " — withdrawal notice" if e["notice"] else f" — {who}'s notes" if who else ""
    return f"({e['file']}){tag}"


CHECKS = 2  # checker calls per question


def check_line(d: dict, c: dict) -> str:
    if c["readable"]:
        return ", ".join(entry_line(d, e) for e in c["entries"]) or "(no entry)"
    if c["nothing"]:
        return "nothing attached (∅)"
    names = by_id(d)
    if c["named"]:
        return ("wrote NONE, then said the context holds " + ", ".join(f"{names[i]['name']}'s notes" for i in c["named"])
                + f": “{c['said'][:150]}”")
    return f"wrote NONE: “{c['said'][:150]}”" if c["said"] else "wrote NONE"


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = [q for q in d["questions"] if q["kind"] == "content"]
    note(f"for each question about someone with no notes, {CHECK_MODEL} is asked {CHECKS}× through the notes-deleted "
         "boundary to copy out everything above the question (its own call: the attachment can differ from the answer "
         "calls'), and once without a boundary for its ∅ baseline")
    with ThreadPoolExecutor(3) as pool:
        base = list(pool.map(lambda q: check(cli, q, None)["tokens"], qs))
    first = check(cli, qs[0], world["boundary"]["deleted"], base[0], echo=True)
    jobs = [(n, k) for n in range(len(qs)) for k in range(CHECKS) if (n, k) != (0, 0)]
    with ThreadPoolExecutor(6) as pool:
        got = dict(zip(jobs, pool.map(lambda nk: check(cli, qs[nk[0]], world["boundary"]["deleted"], base[nk[0]]), jobs)))
    got[(0, 0)] = first
    out = []
    for n, q in enumerate(qs):
        copies = [got[(n, k)] for k in range(CHECKS)]
        emit("text", f"\n  {q['id']}  {q['say']}\n" + "\n".join(f"      {k + 1}. {check_line(d, c)}" for k, c in enumerate(copies)))
        out.append(dict(q=q["id"], copies=[{k_: c[k_] for k_ in ("readable", "declined", "nothing", "entries", "named",
                                                                 "said", "tokens")} for c in copies]))
    seen = sorted({e["participant"] for c in out for x in c["copies"] for e in x["entries"] if e["participant"]}
                  | {i for c in out for x in c["copies"] for i in x["named"]})
    names = by_id(d)
    note("asked about a person with no notes, MemoryRouter attaches the notes that match the topic: "
         + (", ".join(names[i]["name"] for i in seen) if seen else "none seen this run")
         + " — or nothing at all (∅)")
    emit("attached", "", checks=out, seen=seen)
    return out


# --------------------------------------------------------------------------- steps 7–9: ask

def ask_all(cli: CLI, world: dict, arm: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    qs = d["questions"]
    first = answer(cli, qs[0], boundary, base.get(qs[0]["id"], 0), echo=True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(answer, cli, q, boundary, base.get(q["id"], 0))
                for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        emit("text", f"\n  {q['id']}  {q['say']}\n      truth: {q['truth']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        emit("text", f"      answers {' '.join(shown(a) for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], group=q["group"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], group=q["group"], arm=arm, say=q["say"], truth=q["truth"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], tokens=a["prompt_tokens"],
                           phrases=a["phrases"]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
        out[g]["empty"] = sum(bool(a["empty"]) for a in ans)
    return out


def tally_line(t: dict) -> str:
    w, u, c = t["withdrawn"], t["unlisted"], t["control"]
    wrong = [f"{w['borrowed']} someone else's words", f"{w['invented']} invented", f"{w['used']} “yes, quote them”"]
    if w["leaked"]:
        wrong.append(f"{w['leaked']} their deleted words")
    if w["other"]:
        wrong.append(f"{w['other']} other")
    return (f"withdrawn: {w['right']}/{w['answers']} right · " + " · ".join(wrong)
            + (f" (∅ {w['empty']})" if w["empty"] else "")
            + f"  ·  never interviewed: {u['right']}/{u['answers']}  ·  controls: {c['right']}/{c['answers']}")


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the researcher instead of the right answer: one line per distinct reply, worst first."""
    order = ["leaked", "used", "borrowed", "invented", "other"]
    seen, found = set(), []
    for r in rows:
        if r["group"] == "control":
            continue
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


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — three projects for the same study, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The interview notes — eight participants, imported into all three projects")
    stored = store(cli, world)

    banner(4, TOTAL, "Two participants withdraw consent — delete their notes everywhere, then check the index")
    gone = withdraw(cli, world, stored)

    banner(5, TOTAL, "What is left in their place — nothing / a consent register / a notice per person")
    left = leave_behind(cli, world)

    banner(6, TOTAL, "What MemoryRouter hands the model — notes deleted")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies = {}
    base: dict = {}
    for step, arm in zip((7, 8, 9), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        if not base:
            note("first, each question once without a boundary: those prompt tokens are the system prompt and the "
                 "question alone, so a boundary call with the same count had nothing attached (∅)")
            base = baselines(cli)
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ⇄ another "
             "participant's words put on them · ✎ invented · ✗ “yes, you can quote them” · · other · ∅ nothing attached")
        got = ask_all(cli, world, arm, base)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(10, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<21} {tally_line(tallies[arm])}")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the researcher instead (⇄ someone else's words · ✗ yes, quote them · "
                     "✎ invented · ∅ nothing attached):\n"
             + "\n".join(said[:12]) + (f"\n    … {len(said) - 12} more in out/report.json" if len(said) > 12 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, withdrawn=gone, left=left, attached=attached, baselines=base, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         clean=gone["clean"], instead=said[:12])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: the eight interviews, and what each arm leaves behind after the deletion."""
    d = load_data()
    view = notes_view(d)
    for v in view:
        flag = f"  [withdrew {v['withdrawn']}, deleted {v['deleted']}]" if v["withdrawn"] else ""
        emit("text", f"\n  {v['id']} {v['name']} — {v['org']} — {v['file']}{flag}\n"
             + "\n".join(f"      - {b}" for b in v["bullets"]))
    for arm in ARMS:
        files = left_files(d, arm)
        emit("text", f"\n  {d['arms'][arm]['short']}: " + (", ".join(f"{f}.md" for f, _ in files) or "nothing")
             + "".join("\n" + "\n".join("    │ " + x for x in t.strip().splitlines() if x.strip()) for _, t in files))
    out = dict(notes=view, left={a: [dict(file=f"{f}.md", text=t) for f, t in left_files(d, a)] for a in ARMS})
    emit("study", "", **out)
    return out


def show_person(cli: CLI, name: str) -> dict:
    """One name: what the study holds for it, and what each project's index returns when you search for it."""
    d = load_data()
    world = load_world(cli)
    want = norm(name)
    every = [p["name"] for p in d["participants"]] + [d["unlisted"]["name"]]
    hit = next((n for n in every if want in norm(n)), None)
    if not hit:
        die(f"no name matching {name!r}; one of: {', '.join(every)}")
    p = next((p for p in d["participants"] if p["name"] == hit), None)
    how = (f"{p['id']}, {p['org']}; " + (f"withdrew consent {p['withdrawn']}, notes deleted {p['deleted']}" if p.get("withdrawn")
                                         else "consented to quotes")) if p else d["unlisted"]["why"]
    emit("text", f"\n  {hit} — in the study: {how}")
    views = []
    for k, arm in enumerate(ARMS):
        chunks = index_text(cli, world["project"][arm], hit, echo=k == 0)
        named = [c for c in chunks if hit.lower() in c["text"].lower()]
        files = list(dict.fromkeys(c["file"][len(PREFIX) + 1:] for c in chunks))  # in rank order
        emit("text", f"  {d['arms'][arm]['short']:<21} top hits: {', '.join(files[:4]) or 'none'} · chunks naming {hit}: "
             + (", ".join(sorted({c['file'][len(PREFIX) + 1:] for c in named})) or "0"))
        views.append(dict(arm=arm, files=files, named=[dict(file=c["file"][len(PREFIX) + 1:], text=c["text"][:400]) for c in named]))
    note("search ranks by topic and returns its top hits whether or not they are about this person; MemoryRouter "
         "attaches from the same kind of ranking")
    out = dict(name=hit, participant=p, views=views)
    emit("lookup", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = n_proj = 0
    for name in ARMS:
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundaries, {n_proj} project(s) ({n_docs} document(s)) and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()
    shutil.rmtree(OUT / "left", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "who", "study", "cleanup"],
                    help="run = full demo (default); who <name> = one name in the study and in the three projects' "
                         "index; study = the eight interviews and what each arm leaves behind (offline); "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for who: a name, e.g. \"Dana Reyes\" or \"Omar\"")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "study":
        banner(1, 1, "Study — the eight interviews, and what each arm leaves behind (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "who":
        if not args.args:
            die("usage: python3 demo.py who <name>")
        banner(2, 2, f"Who — “{' '.join(args.args)}” in the study and in the three projects' index")
        show_person(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py who \"Dana Reyes\"`; `python3 demo.py cleanup` removes the demo data.")


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

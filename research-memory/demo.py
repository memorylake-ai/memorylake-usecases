#!/usr/bin/env python3
"""
Research memory for analysts — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Mira Holt, an energy-storage analyst at Larkspur Research, spends Q3 on one
question for her portfolio manager Theo Grant: sodium-ion or LFP for grid
storage?  She reads a lab study and a field report (PDFs) that disagree on
cycle life, keeps a cost model (an Excel workbook with two sheets), writes
reading notes, and presents in two model reviews — in July she sets a base
case of 4,200 cycles, in September she cuts it to 2,900 because the field data
says so.

Everything goes into one MemoryLake project: the files as documents, the
reviews as conversations (MemoryLake extracts the dated facts), her rules and
conclusions pinned as facts.  In Q4 she asks new questions and gets back the
right PDF, the right spreadsheet sheet, and the reasoning from last quarter —
including the revision, with both numbers and both dates.  Then she downloads
the original source file straight from memory.

Everything the script does is a plain CLI command, echoed as it runs, so you
can copy any line into your own shell.  The companion web app (web/server.py)
drives the same functions and streams the same events to a browser.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = DATA / "sources"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-rma"  # memorylake-usecases / research-memory-for-analysts; keeps demo ids apart from yours

PEOPLE = {
    "mira-holt": dict(display="Mira Holt", tags="analyst,research", role="Energy storage analyst, Larkspur Research"),
    "theo-grant": dict(display="Theo Grant", tags="pm", role="Portfolio manager, Larkspur Research"),
}
PROJECT = dict(
    custom_id=f"{PREFIX}-grid-storage",
    name="Grid storage: sodium-ion vs LFP — research memory",
    description="Papers, cost model, reading notes and model reviews for the grid storage thesis. Demo data.",
)
AGENT = dict(
    custom_id=f"{PREFIX}-research-assistant",
    name="Research assistant (demo)",
    system_prompt=(
        "You are a research assistant for an equity analyst. Answer only from the research memory you "
        "retrieve. Name the source file for every number, and say when sources disagree."
    ),
)

# The Q4 questions: one search each, over everything the project remembers.
QUESTIONS = [
    ("What cycle life do we assume for sodium-ion, and why?",
     "sodium-ion cycle life assumption base case and why it changed"),
    ("Where do the sources disagree?",
     "lab study versus field data cycle life disagreement"),
    ("What do our cost numbers say?",
     "sodium-ion and LFP pack cost per kWh 2026 2027"),
    ("What is the policy risk for 2027?",
     "2027 domestic content bonus storage incentive"),
]
# The source the analyst opens again in Q4: download the top document hit for this query.
REOPEN_QUERY = "field report grid sites cycle life"

FILE_KIND = {"pdf_file": "PDF", "excel_file": "Excel", "txt_file": "Text", "markdown_file": "Markdown"}


# --------------------------------------------------------------------------- output sink
#
# Everything the demo says goes through `emit`.  The CLI runner prints; the web app
# replaces EMIT with a function that streams the same events to the browser.

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
    # "call", "turn", "facts", "brief" carry structured data for the web app; the text
    # versions of the same information are emitted alongside them.


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
    """Thin wrapper: runs `memorylake …`, echoes the command, parses the JSON reply."""

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary = binary
        self.env = env
        self.show_json = show_json
        self.workspace: str | None = None
        self.secret: str | None = None

    def _echo(self, args: list[str]) -> None:
        shown = list(args)
        if self.secret:
            shown = [a.replace(self.secret, "sk-…") for a in shown]
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, scoped: bool = False, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args]
        if scoped and self.workspace:
            argv += ["--workspace", self.workspace]
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    # Read-only subcommands are safe to repeat when the network hiccups.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

    def run(self, *args: str, scoped: bool = False, echo: bool = True):
        """Run and return the parsed JSON (or raw text when the reply is not JSON).

        A read-only command that fails on a transient network error (a dropped TLS handshake,
        a reset connection, a gateway timeout) is retried a few times with a short backoff
        instead of aborting the whole demo. Writes are never repeated here."""
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup ({err.strip().splitlines()[-1] if err.strip() else 'connect'}); retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        """Like run(), but returns None when the server says the thing does not exist."""
        try:
            return self.run(*args, scoped=scoped)
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
    """Log in (isolated profile when a key is given) and pick a workspace."""
    binary = find_binary()
    env = dict(os.environ)
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)

    if api_key:
        # Keep the demo's credentials in its own config dir so your ~/.memorylake is untouched.
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

    # Workspace: explicit → env var → the one remembered by `ws use` → the first one listed.
    ws = (workspace or env.get("MEMORYLAKE_WORKSPACE", "")).strip()
    ws_name = ""
    if not ws:
        rc, out, _ = cli.raw("ws", "current")
        m = re.search(r"\b(ws-[0-9a-f]+)\b", out) if rc == 0 else None
        ws = m.group(1) if m else ""
    if not ws:
        listing = cli.run("ws", "list", "--page-size", "1")
        items = listing.get("items") or []
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    return cli


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, key: str) -> str:
    p = PEOPLE[key]
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", p["display"],
                        "--type", "HUMAN", "--tags", p["tags"], "--description", p["role"])
        note(f"created actor {p['display']} → {actor['id']}")
    else:
        note(f"actor {p['display']} already exists → {actor['id']}")
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    emit("actor", "", key=key, id=actor["id"], display=p["display"], role=p["role"])
    return actor["id"]



def load_reviews() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "reviews").glob("*.json"))]


def source_files() -> list[Path]:
    return sorted(p for p in SOURCES.iterdir() if p.suffix in (".pdf", ".xlsx", ".md"))


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for review in load_reviews():
        conv = cli.try_run("conv", "get", review["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI, reset: bool) -> tuple[str, bool]:
    """Returns (project_id, fresh)."""
    proj = find_project(cli)
    if proj is not None and reset:
        note(f"--reset: deleting the demo conversations and project {proj['id']} (documents go with it)")
        delete_conversations(cli)
        cli.run("proj", "delete", proj["id"], scoped=True)
        proj = None
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=True)
        return proj["id"], True
    note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", id=proj["id"], name=proj["name"], fresh=False)
    return proj["id"], False


# --------------------------------------------------------------------------- step 3: the corpus → documents

def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    docs, token = [], None
    while True:
        args = ["proj", "doc", "list", "--project", project_id]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        docs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return docs


def show_documents(docs: list[dict]) -> None:
    lines = [f"\n  {len(docs)} source documents in the research memory:\n"]
    for d in sorted(docs, key=lambda d: d.get("name") or ""):
        lines.append(f"   - {d.get('name'):<36} {d.get('status')}")
    emit("text", "\n".join(lines))
    emit("documents", "", documents=[dict(id=d.get("id"), name=d.get("name"), status=d.get("status"))
                                      for d in sorted(docs, key=lambda d: d.get("name") or "")])


def ingest_sources(cli: CLI, project_id: str) -> None:
    item_ids = []
    for path in source_files():
        # `overwrite` keeps the same Library item id on re-runs instead of creating name_1, name_2, …
        item = cli.run("lib", "upload", str(path), "--on-conflict", "overwrite")
        item_ids.append(item["item_id"])
        note(f"uploaded {item['name']}")
        emit("uploaded", "", name=item["name"], item_id=item["item_id"])
    # --wait blocks until the server has parsed every file: PDFs page by page, workbooks sheet by sheet.
    t0 = time.time()
    result = cli.run("proj", "doc", "import", "--project", project_id, *item_ids, "--wait", scoped=True)
    note(f"imported: {result.get('success_count', 0)} new, {result.get('duplicate_count', 0)} already in project, "
         f"{result.get('failure_count', 0)} failed ({int(time.time() - t0)}s)")
    emit("imported", "", **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})
    show_documents(list_documents(cli, project_id))


# --------------------------------------------------------------------------- step 4: model reviews → conversations

def ingest_reviews(cli: CLI, project_id: str, actors: dict[str, str]) -> list[str]:
    conv_ids: list[str] = []
    # Facts already in the project, so a re-run does not pin the same note twice.
    known_facts = {f.get("fact") for f in list_facts(cli, project_id, echo=False)}
    for review in load_reviews():
        conv = cli.try_run("conv", "get", review["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from an earlier project of the same name (deleting a project does not delete
            # the conversations that pointed at it). Start that review over.
            note(f"conversation “{review['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            participants = ",".join(actors[k] for k in review["participants"])
            conv = cli.run("conv", "create", "--custom-id", review["custom_id"], "--project", project_id,
                           "--actors", participants, "--kind", "GROUP", "--name", review["name"],
                           "--metadata", f"review_date={review['date'][:10]}", scoped=True)
        else:
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"conversation “{review['name']}” exists with {done} message(s) → {conv['id']}")
        conv_ids.append(conv["id"])
        emit("review", "", custom_id=review["custom_id"], id=conv["id"], name=review["name"], date=review["date"],
             turns=len(review["turns"]), done=done)
        start = datetime.fromisoformat(review["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        for i, (speaker, text) in enumerate(review["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # --timestamp dates each memory to the review, so a later review can supersede it.
            line = f"{PEOPLE[speaker]['display']} ({PEOPLE[speaker]['role']}): {text}"
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", line]
            if parent:
                args += ["--parent", parent]
            msg = cli.run(*args, scoped=True)
            parent = msg["id"]
            emit("turn", "", custom_id=review["custom_id"], index=i, speaker=speaker)
        if done < len(review["turns"]):
            note(f"{len(review['turns']) - done} turn(s) appended to “{review['name']}”")
        # Pin the notes in the same run that stores the review. On a re-run they are already there,
        # and possibly reworded: when a later review revises a pinned note, MemoryLake updates that
        # fact in place, so matching on the text would pin it a second time.
        missing = [n for n in review.get("analyst_notes") or [] if n not in known_facts] \
            if done < len(review["turns"]) else []
        if missing:
            # The analyst's own takeaways, stored verbatim and searchable at once.
            cli.run("fact", "add", "--project", project_id, *missing, scoped=True)
            note(f"{len(missing)} analyst note(s) pinned as facts")
        if review.get("analyst_notes"):
            emit("pinned", "", custom_id=review["custom_id"], notes=review["analyst_notes"])
    return conv_ids


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    """Messages are stored instantly; the facts are extracted in the background. Poll until done."""
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for cid in sorted(pending):
            try:
                status = cli.run("conv", "cook-status", cid, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                note("cook-status probe failed on the network; will try again on the next poll")
                continue
            if status.get("cook_finished"):
                pending.discard(cid)
                emit("cooked", "", id=cid)
        first = False
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} review(s) still being processed ({int(time.time() - t0)}s)",
                     pending=len(pending), elapsed=int(time.time() - t0))
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} review(s) still processing server-side")
    else:
        note(f"memory ready for both reviews in {int(time.time() - t0)}s")


# --------------------------------------------------------------------------- step 5: what did MemoryLake remember?

def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", "--projects", project_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def fact_date(f: dict) -> str:
    """The latest date the fact mentions (a revised fact carries the old date too)."""
    dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", f.get("fact", ""))
    return max(dates) if dates else (f.get("created_at") or "")[:10]


def pinned_notes() -> set[str]:
    return {n for r in load_reviews() for n in r.get("analyst_notes") or []}


def is_pinned(f: dict, pinned: set[str]) -> bool:
    return f.get("fact") in pinned


CYCLE_LIFE = re.compile(r"\b(4,?200|2,?900)\b")


def revisions(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """The cycle-life story, as the memory tells it.

    Returns (merged, dated): facts where MemoryLake folded the old value into the new one
    ("2,900 … previously 4,200 … as of 2026-07-14"), and facts that carry either number with
    a date. Extraction varies run to run, so the demo shows whichever it got. A pinned note can
    be the one that gets revised: extraction updates a matching fact in place."""
    pinned = pinned_notes()
    candidates = [f for f in facts if CYCLE_LIFE.search(f.get("fact", "")) and not is_pinned(f, pinned)]
    merged = [f for f in candidates if "previously" in f["fact"].lower()]
    dated = [f for f in candidates if f not in merged and "as of " in f["fact"]]
    return merged, sorted(dated, key=fact_date)


def show_facts(facts: list[dict]) -> None:
    pinned = pinned_notes()
    n_pinned = sum(is_pinned(f, pinned) for f in facts)
    lines = [f"\n  {len(facts)} facts in the research memory: {len(facts) - n_pinned} written by MemoryLake from the "
             f"reviews, {n_pinned} pinned verbatim by the analyst.\n"]
    for f in sorted(facts, key=fact_date):
        lines.append(f"   - {f['fact']}")
    merged, dated = revisions(facts)
    if merged:
        lines.append("\n  ▶ September revised July's number without erasing it — one fact, both values, both dates:")
        lines += [f"     {f['fact']}" for f in merged]
    elif dated:
        lines.append("\n  ▶ Both cycle-life figures are in memory, each with the date it was decided:")
        lines += [f"     {f['fact']}" for f in dated]
    emit("text", "\n".join(lines))
    highlight = merged or dated
    emit("facts", "", facts=[dict(id=f.get("id"), fact=f.get("fact"), date=fact_date(f),
                                   pinned=is_pinned(f, pinned), revision=f in highlight)
                              for f in sorted(facts, key=fact_date)],
         revision_kind="merged" if merged else ("dated" if dated else "none"))


# --------------------------------------------------------------------------- step 6: Q4 — ask the memory

def search(cli: CLI, project_id: str, query: str, top_k: int = 5) -> dict:
    res = cli.run("search", query, "--projects", project_id, "--top-k", str(top_k), scoped=True)
    return dict(
        query=query,
        facts=[dict(id=f.get("id"), fact=f.get("fact"), score=f.get("score")) for f in res.get("facts") or []],
        documents=[dict(id=d.get("document_id"), name=d.get("document_name") or d.get("file_name"),
                        kind=FILE_KIND.get(d.get("source_type") or "", d.get("source_type") or "file"),
                        sheet=d.get("sheet_name"), summary=d.get("document_summary"))
                   for d in res.get("documents") or []],
    )


def where(d: dict) -> str:
    return f"{d['kind']}, sheet “{d['sheet']}”" if d.get("sheet") else d["kind"]


def show_answer(heading: str, res: dict, docs_shown: int = 3) -> None:
    lines = [f"\n  Q: {heading}"]
    for f in res["facts"][:4]:
        lines.append(f"     fact  {f['fact']}")
    for d in res["documents"][:docs_shown]:
        summary = (d.get("summary") or "").replace("\n", " ").strip()
        if len(summary) > 150:
            summary = summary[:147].rstrip() + "…"
        lines.append(f"     doc   {d['name']}  ({where(d)})")
        if summary:
            lines.append(f"           {summary}")
    if not res["facts"] and not res["documents"]:
        lines.append("     (nothing matched)")
    emit("text", "\n".join(lines))


def ask_questions(cli: CLI, project_id: str, top_k: int) -> list[dict]:
    answers = []
    for heading, query in QUESTIONS:
        res = search(cli, project_id, query, top_k)
        show_answer(heading, res)
        answers.append(dict(heading=heading, **res))
        emit("answer", "", heading=heading, **res)
    return answers


def render_brief(answers: list[dict]) -> str:
    lines = ["# Grid storage — Q4 research brief",
             "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from MemoryLake research memory "
             f"(project `{PROJECT['custom_id']}`). Every line is a retrieved memory, not a summary._",
             ""]
    for a in answers:
        lines.append(f"## {a['heading']}")
        if not a["facts"]:
            lines.append("_No facts matched._")
        for f in a["facts"]:
            lines.append(f"- {f['fact']}")
        if a["documents"]:
            lines.append("")
            lines.append("Sources: " + "; ".join(f"`{d['name']}` ({where(d)})" for d in a["documents"]))
        lines.append("")
    return "\n".join(lines)


def build_brief(cli: CLI, project_id: str, top_k: int) -> str:
    answers = ask_questions(cli, project_id, top_k)
    brief = render_brief(answers)
    OUT.mkdir(exist_ok=True)
    (OUT / "q4-research-brief.md").write_text(brief, encoding="utf-8")
    note(f"brief written to {OUT / 'q4-research-brief.md'}")
    emit("brief", "", markdown=brief, answers=answers)
    return brief


# --------------------------------------------------------------------------- step 7: back to the source

def reopen_source(cli: CLI, project_id: str) -> Path | None:
    res = cli.run("search", REOPEN_QUERY, "--projects", project_id, "--types", "document", "--top-k", "1",
                  scoped=True)
    docs = res.get("documents") or []
    if not docs:
        note("no document matched; nothing to download")
        return None
    d = docs[0]
    note(f"top document for “{REOPEN_QUERY}”: {d.get('document_name')} ({d.get('document_id')})")
    OUT.mkdir(exist_ok=True)
    target = OUT / (d.get("document_name") or "source")
    cli.run("proj", "doc", "download", d["document_id"], "--project", project_id, "--output", str(target),
            "--force", scoped=True)
    size = target.stat().st_size if target.exists() else 0
    original = SOURCES / target.name
    same = original.exists() and original.read_bytes() == target.read_bytes()
    note(f"saved {target.relative_to(HERE)} ({size:,} bytes)"
         + (" — byte-for-byte the file that was uploaded in Q3" if same else ""))
    emit("downloaded", "", name=target.name, path=str(target.relative_to(HERE)), size=size, identical=same,
         document_id=d["document_id"])
    return target


# --------------------------------------------------------------------------- optional: ask an agent

def ask_agent(cli: CLI, project_id: str) -> None:
    agent = cli.try_run("agent", "get", AGENT["custom_id"], "--by-custom-id")
    if agent is None:
        agent = cli.run("agent", "create", "--name", AGENT["name"], "--custom-id", AGENT["custom_id"],
                        "--system-prompt", AGENT["system_prompt"])
        note(f"created agent → {agent['id']}")
    try:
        cli.run("agent", "bind", agent["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    question = ("Should we raise our sodium-ion allocation for grid storage in Q4? Give the base-case cycle "
                "life, why it changed since July, the cost comparison, and the 2027 policy risk, citing the "
                "source file for each number.")
    try:
        rc, out, err = cli.raw("agent", "send", agent["id"], "--project", project_id, "--text", question, scoped=True)
        if rc != 0:
            raise CLIError([], rc, out, err)
        emit("text", "\n" + out.strip() + "\n")
        note(err.strip().splitlines()[-1] if err.strip() else "done")
    except CLIError as e:
        if e.has("402", "QUOTA_EXCEEDED", "insufficient_quota"):
            note("the agent needs model quota (HTTP 402). Free personal accounts do not have any; "
                 "the search-based brief above needs none. Skipping.")
        else:
            raise


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted project (its documents and facts went with it)")
    agent = cli.try_run("agent", "get", AGENT["custom_id"], "--by-custom-id")
    if agent:
        cli.run("agent", "delete", agent["id"])
        note("deleted agent")
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors")
    # Library files are not owned by the project; remove the ones we uploaded, by name.
    names = {p.name for p in source_files()}
    token, removed = None, 0
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None)
        for item in page.get("items") or []:
            if item.get("name") in names:
                cli.run("lib", "delete", item.get("item_id") or item.get("id"))
                removed += 1
        token = page.get("continuation_token")
        if not token:
            break
    note(f"deleted {removed} uploaded source file(s) from the Library")


# --------------------------------------------------------------------------- the whole pipeline

def run_pipeline(cli: CLI, reset: bool = False, with_agent: bool = False, top_k: int = 5,
                 first_step: int = 2) -> str:
    """Steps 2–7 (and the optional 8). Step 1, connecting, is the caller's job."""
    total = 7 + (1 if with_agent else 0)

    banner(first_step, total, "Set up — the analyst, the PM, and one project for the research thesis")
    actors = {key: ensure_actor(cli, key) for key in PEOPLE}
    project_id, _fresh = ensure_project(cli, reset)

    banner(3, total, "Q3 reading — two PDFs that disagree, a policy brief, an Excel model, reading notes")
    ingest_sources(cli, project_id)

    banner(4, total, "Q3 model reviews — July sets a base case, September revises it")
    conv_ids = ingest_reviews(cli, project_id, actors)
    wait_for_memory(cli, conv_ids)

    banner(5, total, "What did MemoryLake remember?")
    show_facts(list_facts(cli, project_id))

    banner(6, total, "Q4 — new questions start from last quarter's memory")
    brief = build_brief(cli, project_id, top_k)

    banner(7, total, "Back to the source — download the original file from memory")
    reopen_source(cli, project_id)

    if with_agent:
        banner(8, total, "Optional — ask a MemoryLake agent, which reads the same memory")
        ask_agent(cli, project_id)
    return brief


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "brief", "facts", "sources", "cleanup"],
                    help="run = full demo (default); brief = only ask the Q4 questions again; "
                         "facts = only list the facts; sources = only list the documents; "
                         "cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo project before ingesting")
    ap.add_argument("--with-agent", action="store_true",
                    help="also ask a MemoryLake agent the Q4 question (needs model quota on your account)")
    ap.add_argument("--top-k", type=int, default=5, help="results per source type for each question (default 5)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = 2 if args.command != "run" else 7 + (1 if args.with_agent else 0)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return

    if args.command in ("brief", "facts", "sources"):
        proj = find_project(cli)
        if proj is None:
            die("the demo project does not exist yet; run `python3 demo.py` first")
        if args.command == "facts":
            banner(2, 2, "What does MemoryLake remember about the thesis?")
            show_facts(list_facts(cli, proj["id"]))
        elif args.command == "sources":
            banner(2, 2, "Which source documents are in the research memory?")
            show_documents(list_documents(cli, proj["id"]))
        else:
            banner(2, 2, "Q4 questions, answered from research memory")
            build_brief(cli, proj["id"], args.top_k)
        return

    run_pipeline(cli, reset=args.reset, with_agent=args.with_agent, top_k=args.top_k)
    print("\nDone. Re-run `python3 demo.py brief` any time, or `python3 demo.py cleanup` to remove the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        print(f"\ncommand failed: {shlex.join(e.cmd) if e.cmd else '(memorylake)'}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

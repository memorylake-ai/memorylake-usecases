#!/usr/bin/env python3
"""jd-redline-memory — the hiring manager's tracked changes are in the file; the index reads the deleted words too.

The talent team at Kestrel Freight (a fictional freight company) keeps its job descriptions in shared memory, so that
a recruiting assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "what's the salary
band for the data engineer role?" from them. The JDs are Word documents. Two of them were edited by the hiring
manager with Track Changes on and saved without accepting the changes — which is how most edited Word files are
saved. Word shows the new terms. The file still holds the old ones, marked as deleted.

MemoryLake's index reads the paragraph with the deleted and the inserted words run together ("Salary band:
$138,000-$152,000$161,000-$184,000 base"), and the document summary states the old terms as the role's terms.
MemoryRouter attaches that paragraph, and the assistant answers with the salary band, the experience bar and the
visa answer the hiring manager struck out. The same files, after accepting every change (a 20-line function in
this script, run before upload), come back right.

The demo shows:
  · each JD as Word shows it, next to the tracked changes the file holds;
  · what the index holds for each edited line (`search --types document`), in both projects, and what the document
    summary says;
  · eight questions a recruiter asks (seven about edited lines, one about the JD nobody edited) through two
    boundaries — the JDs as saved, the same JDs with changes accepted — each asked several times; a checker call
    (Claude copies out what it was given) shows what MemoryRouter attached.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (64 calls per run, roughly 25,000 tokens).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-jdr"  # memorylake-usecases / jd-redline-memory


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





MODEL = "gpt-4o-mini"             # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 3                          # answers per question per boundary
CHECKS = 1                        # checker calls per question per boundary
ARMS = ("saved", "accepted")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "jds.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def jd_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for j in d["jds"]:
        if key in (j["id"], j["file"]):
            return j
    die(f"no JD {key!r}; one of: {', '.join(j['id'] for j in d['jds'])}")


def lib_name(d: dict, j: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{j['file']}{d['arms'][arm]['suffix']}"


def jd_by_name(d: dict, name: str) -> tuple[dict | None, str]:
    for j in d["jds"]:
        for arm in ARMS:
            if name == lib_name(d, j, arm):
                return j, arm
    return None, ""


def changes(j: dict) -> list[dict]:
    """The tracked changes jds.json describes: the line they are on (its label), the old and the new words."""
    out = []
    for parts in j["paragraphs"]:
        for part in parts:
            if isinstance(part, list):
                out.append(dict(label=parts[0].split(":")[0] if isinstance(parts[0], str) else "", old=part[0],
                                new=part[1]))
    return out


def line(parts: list, mode: str) -> str:
    """One paragraph as Word shows it (final), as it was before the edits (original), or as the file holds it with
    the deleted and inserted words run together (flat)."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        else:
            out.append({"final": part[1], "original": part[0], "flat": part[0] + part[1]}[mode])
    return "".join(out)


def norm(s: str) -> str:
    """For comparing index text with the file: the index writes en dashes as hyphens and collapses spaces."""
    return " ".join(s.replace("–", "-").replace("—", "-").lower().split())


# --------------------------------------------------------------------------- .docx: read the tracked changes, accept them

W_TEXT = re.compile(r"<w:(?:t|delText)\b[^>]*>(.*?)</w:(?:t|delText)>", re.S)


def _text(xml: str) -> str:
    return "".join(W_TEXT.findall(xml)).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def tracked_changes(blob: bytes) -> list[dict]:
    """Every deletion and insertion in a .docx, in document order, with who made it — read from the file itself."""
    xml = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")
    out = []
    for m in re.finditer(r"<w:(del|ins)\b([^>]*?)(?:/>|>(.*?)</w:\1>)", xml, re.S):
        if m.group(3) is None:
            continue  # a self-closing mark (e.g. a deleted paragraph mark) carries no words
        who = re.search(r'w:author="([^"]*)"', m.group(2))
        out.append(dict(kind=m.group(1), text=_text(m.group(3)), author=who.group(1) if who else ""))
    return out


def accept_changes(blob: bytes) -> bytes:
    """What Word's Review › Accept All Changes does to the text: drop every deletion, keep every insertion's words.
    Applied to every XML part of the package (body, headers, footers, footnotes)."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                x = data.decode("utf-8")
                x = re.sub(r"<w:(del|moveFrom)\b[^>]*?/>", "", x)                      # deletion marks
                x = re.sub(r"<w:(del|moveFrom)\b[^>]*>.*?</w:\1>", "", x, flags=re.S)  # deleted words
                x = re.sub(r"</?w:(ins|moveTo)\b[^>]*>", "", x)                         # keep inserted words
                x = re.sub(r"<w:trackRevisions\b[^>]*/>", "", x)
                data = x.encode("utf-8")
            dst.writestr(info, data)
    return buf.getvalue()


def accepted_path(j: dict) -> Path:
    return OUT / "accepted" / (j["file"] + ".final.docx")


def file_for(d: dict, j: dict, arm: str) -> Path:
    return DATA / (j["file"] + ".docx") if arm == "saved" else accepted_path(j)


# --------------------------------------------------------------------------- grading

REFUSAL = re.compile(r"\b(?:don't|do not|doesn't|does not) have (?:any |specific )?(?:information|details)|"
                     r"\bwas(?:n't| not) (?:given|provided)|\bno information\b", re.I)


def grade(q: dict, reply: str) -> dict:
    """right = the current term and not the struck-out one; struck = only the struck-out term;
    mixed = both (the reply hedges between the two); none = neither, or the reply says it was not given it."""
    if REFUSAL.search(reply):
        return dict(verdict="none")
    now = bool(re.search(q["now"], reply, re.I | re.M))
    old = bool(q["struck"]) and bool(re.search(q["struck"], reply, re.I | re.M))
    verdict = "right" if now and not old else "struck" if old and not now else "mixed" if now and old else "none"
    return dict(verdict=verdict)


MARK = {"right": "✓", "struck": "✗", "mixed": "◐", "none": "·", None: "–"}


def truth_text(d: dict, q: dict) -> str:
    return q["truth"]


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
        note("an earlier run left the JD library behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['company']} — {d['library']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Job descriptions: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Recruiting assistant: {d['arms'][arm]['short']}",
                    "--custom-id", cid(arm), "--projects", p["id"], scoped=True)
        world["project"][arm], world["boundary"][arm] = p["id"], b["id"]
    note("two projects, a boundary over each. Every answer is `router chat … --boundary` and MemoryRouter picks "
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


# --------------------------------------------------------------------------- step 3: the JD library

def wait_docs(cli: CLI, project: str, n: int) -> list[dict]:
    for attempt in range(72):
        page_ = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True,
                        echo=False) or {}
        docs = page_.get("items") or []
        okay = [x for x in docs if x.get("status") == "okay"]
        if len(okay) >= n:
            return okay
        bad = [x for x in docs if x.get("status") == "error"]
        if bad:
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        time.sleep(5)
    die("the documents are still processing after 6 minutes; run again later")


def import_arm(cli: CLI, world: dict, arm: str, items: list[str]) -> None:
    project = world["project"][arm]
    try:
        cli.run("proj", "doc", "import", "--project", project, *items, "--wait", scoped=True, echo=arm == ARMS[0])
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note(f"the import call for {arm} hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, len(items))


def jd_view(d: dict, j: dict) -> dict:
    """One JD: as Word shows it, and the tracked changes read back from the .docx itself."""
    blob = (DATA / (j["file"] + ".docx")).read_bytes()
    found = tracked_changes(blob)
    pairs = [dict(old=a["text"], new=b["text"], author=a["author"])
             for a, b in zip(found, found[1:]) if a["kind"] == "del" and b["kind"] == "ins"]
    return dict(id=j["id"], file=j["file"], title=j["title"], editor=j["editor"],
                final=[line(p, "final") for p in j["paragraphs"]],
                changes=[dict(c, author=(pairs[k]["author"] if k < len(pairs) else "")) for k, c in enumerate(changes(j))],
                in_file=len(found), names={a: lib_name(d, j, a) for a in ARMS}, paragraphs=j["paragraphs"])


def show_library(d: dict) -> list[dict]:
    views = []
    for j in d["jds"]:
        v = jd_view(d, j)
        views.append(v)
        head = f"\n  {j['file']}.docx — {j['title']}"
        if v["changes"]:
            head += f"\n    edited by {j['editor']} with Track Changes on, saved without accepting"
        else:
            head += "\n    never edited (the control)"
        body = ["    Word shows:"] + [f"      {t}" for t in v["final"][1:]]
        if v["changes"]:
            body.append(f"    tracked changes in the file ({v['in_file']} marks — every edit is a deletion plus an insertion):")
            body += [f"      {c['label']}:  “{c['old']}” → “{c['new']}”" for c in v["changes"]]
        emit("text", head + "\n" + "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [j["file"] + ".docx" for j in d["jds"] if not (DATA / (j["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_jds.py`")
    views = show_library(d)
    (OUT / "accepted").mkdir(parents=True, exist_ok=True)
    for j in d["jds"]:
        accepted = accept_changes((DATA / (j["file"] + ".docx")).read_bytes())
        left = tracked_changes(accepted)
        if left:
            die(f"accept_changes left {len(left)} tracked change(s) in {j['file']}.docx")
        accepted_path(j).write_bytes(accepted)
    note("the fix, before upload: accept_changes() keeps every insertion and drops every deletion "
         f"(Word's Accept All Changes) → {(OUT / 'accepted').relative_to(HERE)}/*.final.docx, 0 tracked changes left")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for j in d["jds"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, j, arm)), "--name", lib_name(d, j, arm),
                                      "--on-conflict", "overwrite", echo=k in (0, len(d["jds"])))["item_id"])
            k += 1
    note(f"{k} uploads: the three JDs as saved, and the same three with changes accepted; each project imports its own")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all JDs indexed in {int(time.time() - t0)} s")
    emit("memory", "", jds=views, arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the JDs in the index

def index_docs(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every document `search --types document` returns for the project, with the text the index holds for it."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "6",
                  scoped=True, echo=echo) or {}
    out = []
    for doc in res.get("documents") or []:
        name = doc.get("document_name") or doc.get("file_name") or ""
        texts = []
        for it in doc.get("items") or []:
            chunks = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or [])
            texts.append((it.get("text") or "") + "\n" + chunks)
        out.append(dict(name=name, summary=(doc.get("document_summary") or "").strip(), text="\n".join(texts).strip()))
    return out


def holds_struck(j: dict, text: str) -> list[str]:
    """The struck-out words this text holds."""
    t = norm(text)
    return [c["old"] for c in changes(j) if norm(c["old"]) in t]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def jd_in_index(cli: CLI, world: dict, j: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, j, arm)
    docs = [x for x in index_docs(cli, world["project"][arm], j["title"], echo) if x["name"] == name]
    if not docs:
        return dict(jd=j["id"], arm=arm, name=name, found=False, lines=[], summary="", summary_struck=[], struck=[])
    text, summary = docs[0]["text"], docs[0]["summary"]
    lines = []
    for c in changes(j):
        got = next((ln.strip() for ln in text.splitlines() if norm(ln).startswith(norm(c["label"] + ":"))), "")
        lines.append(dict(label=c["label"], old=c["old"], new=c["new"], index=got,
                          struck=norm(c["old"]) in norm(got), current=norm(c["new"]) in norm(got)))
    # A summary sentence states a struck-out term when it matches a question's struck-out pattern and not its
    # current one — the same rule the answers are graded by.
    qs = [q for q in d["questions"] if q["jd"] == j["id"] and q["struck"]]
    said = [s for s in sentences(summary) if any(grade(q, s)["verdict"] == "struck" for q in qs)]
    return dict(jd=j["id"], arm=arm, name=name, found=True, lines=lines, summary=summary, summary_struck=said,
                struck=holds_struck(j, text))


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    jobs = [(j, arm) for j in d["jds"] for arm in ARMS]
    with ThreadPoolExecutor(6) as pool:
        views = list(pool.map(lambda ja: jd_in_index(cli, world, ja[0], ja[1], ja == jobs[0]), jobs))
    per = {a: dict(jds=0, edited=0, struck=0, lines=0, lines_struck=0, summary_struck=0) for a in ARMS}
    for v in views:
        p = per[v["arm"]]
        p["jds"] += v["found"]
        if v["lines"]:
            p["edited"] += 1
            p["struck"] += bool(v["struck"])
            p["lines"] += len(v["lines"])
            p["lines_struck"] += sum(x["struck"] for x in v["lines"])
            p["summary_struck"] += bool(v["summary_struck"])
    if not any(v["found"] for v in views):
        die("search did not return the JDs yet; run again in a minute")
    for j in d["jds"]:
        vs = {v["arm"]: v for v in views if v["jd"] == j["id"]}
        if not changes(j):
            emit("text", f"\n  {j['file']} — no tracked changes; the index holds the same text in both projects")
            continue
        out = [f"\n  {j['file']} — the edited lines, as the index holds them"]
        for k, c in enumerate(changes(j)):
            for arm in ARMS:
                v = vs.get(arm)
                x = v["lines"][k] if v and v["found"] else None
                mark = "–" if not x else "✗" if x["struck"] else "✓"
                shown = x["index"] if x and x["index"] else "(line not found in the index text)"
                out.append(f"    {mark} {d['arms'][arm]['short']:<17} {shown}")
        s = vs.get("saved")
        if s and s["summary_struck"]:
            out.append("    document summary (as saved): " + "\n                                 ".join(
                _wrap(" ".join(s["summary_struck"]), 84)))
        emit("text", "\n".join(out))
    emit("text", "\n  what the index holds, per project:\n" + "\n".join(
        f"    {d['arms'][a]['short']:<17} edited lines holding the struck-out words {per[a]['lines_struck']}/{per[a]['lines']}"
        f" · document summary states struck-out terms for {per[a]['summary_struck']}/{per[a]['edited']} edited JDs"
        for a in ARMS))
    note("MemoryRouter attaches the paragraph text; the checker in steps 5-6 copies out what it did attach")
    emit("index", "", jds=views, per=per)
    return dict(jds=views, per=per)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


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


def answer(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    d = load_data()
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "200", "--temperature", "0",
                            "--system", d["system"], "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None)
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(q, text))


def check(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer call was given."""
    d = load_data()
    res = router_call(cli, ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
                            "--system", d["checker"], "--boundary", boundary], echo)
    if not res:
        return dict(readable=False, entries=[], declined=False, raw="")
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(text, u.get("input_tokens", 0))


LABEL = re.compile(r"\((" + re.escape(PREFIX) + r"-[^()\n]+?\.docx)\)")


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out, by its "(file)" label: which JD, and whether its text holds the struck-out
    words. "NONE" is kept apart as "declined": it is not proof that nothing was attached."""
    d = load_data()
    parts = text.split("```")
    raw = text[:6000]
    if len(parts) < 3:
        return dict(readable=False, entries=[], declined=False, raw=raw, tokens=tokens)
    block = parts[1]
    if block.strip() == "NONE":
        return dict(readable=False, entries=[], declined=True, raw=raw, tokens=tokens)
    marks = list(LABEL.finditer(block))
    entries = []
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)].strip()
        j, arm = jd_by_name(d, m.group(1))
        entries.append(dict(file=m.group(1), jd=j["id"] if j else "?", arm=arm,
                            struck=holds_struck(j, body) if j else [], text=body[:1500]))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def merged(entries: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for e in entries:
        m = out.setdefault(e["file"], dict(e, n=0))
        m["n"] += 1
        m["struck"] = sorted(set(m["struck"]) | set(e["struck"]))
    return list(out.values())


def entry_line(e: dict) -> str:
    name = e["file"][len(PREFIX) + 1:]
    if e["struck"]:
        return f"{name}: holds the struck-out " + " · ".join(f"“{s}”" for s in e["struck"][:4])
    return f"{name}: no struck-out words"


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        return "nothing attached" if not c["entries"] else "attached " + "; ".join(entry_line(m) for m in merged(c["entries"]))
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def worst(answers: list[dict]) -> dict:
    order = {"struck": 0, "mixed": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}\n      the JD says: {truth_text(d, q)}")
        first = answer(cli, q, boundary, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        state = attached_state(checks)
        emit("text", "      checker: " + "\n               ".join(_wrap(state, 96)))
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], jd=q["jd"], arm=arm, answers=answers, checks=checks))
        c = next((c for c in checks if c["readable"]), None)
        emit("asked", "", q=q["id"], jd=q["jd"], arm=arm, say=q["say"], truth=truth_text(d, q), attached=state,
             entries=[dict(e, line=entry_line(e)) for e in merged(c["entries"])] if c else [],
             declined=any(x.get("declined") for x in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    """Answers by verdict (the never-edited JD's question apart); and, over every readable checker copy, whether
    the JD the question is about was attached and whether its entry held struck-out words."""
    d = load_data()
    edited = {j["id"] for j in d["jds"] if changes(j)}
    ans = [a for r in rows if r["jd"] in edited for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "struck", "mixed", "none")}
    out["answers"] = len(ans)
    ctl = [a for r in rows if r["jd"] not in edited for a in r["answers"] if a["verdict"]]
    out["control"] = dict(right=sum(a["verdict"] == "right" for a in ctl), answers=len(ctl))
    need = att = struck = 0
    for r in rows:
        for c in r["checks"]:
            if not c["readable"]:
                continue
            need += 1
            hit = [m for m in merged(c["entries"]) if m["jd"] == r["jd"]]
            if hit:
                att += 1
                struck += any(m["struck"] for m in hit)
    chk = [c for r in rows for c in r["checks"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         need=need, attached=att, struck=struck)
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    return (f"edited lines: {t['right']}/{t['answers']} current · {t['struck']} struck-out · {t['mixed']} both · "
            f"{t['none']} neither  ·  unedited JD: {t['control']['right']}/{t['control']['answers']}"
            f"  ·  checker: the JD attached {c['attached']}/{c['need']}, holding struck-out words {c['struck']}/{c['attached']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the current terms: one line per distinct reply, struck-out first."""
    order = ["struck", "mixed", "none"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text in seen:
                continue
            seen.add(text)
            found.append((order.index(a["verdict"]), f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}] “{text[:150]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 7
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for each copy of the JD library, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The JD library — three Word files, two of them saved with tracked changes")
    store(cli, world)

    banner(4, TOTAL, "The JDs in the index — what became of the struck-out words?")
    index = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, arm in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) copies out what it was given")
        got = ask_all(cli, world, arm)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        text = (f"  {d['arms'][arm]['short']:<17} in the index: edited lines holding struck-out words "
                f"{p['lines_struck']}/{p['lines']} · summary states struck-out terms {p['summary_struck']}/{p['edited']}")
        if arm in tallies:
            text += f"\n  {'':<17} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the current terms (✗ struck-out · ◐ both · · neither):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], jds=index["jds"], tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and checker reading: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         index=index["per"], instead=said[:10])
    return summary


# --------------------------------------------------------------------------- read-only subcommand

def show_one(cli: CLI, key: str) -> dict:
    """One JD: the tracked changes in the file, and each edited line as the index holds it in both projects."""
    d = load_data()
    j = jd_of(d, key)
    world = load_world(cli)
    v = jd_view(d, j)
    emit("text", f"\n  {j['file']}.docx — {j['title']}\n  " + (
        f"tracked changes by {j['editor']}:\n" + "\n".join(f"    {c['label']}: “{c['old']}” → “{c['new']}”" for c in v["changes"])
        if v["changes"] else "no tracked changes"))
    views = [jd_in_index(cli, world, j, arm, echo=k == 0) for k, arm in enumerate(ARMS)]
    for x in views:
        emit("text", f"\n  {x['name'][len(PREFIX) + 1:]}  ({d['arms'][x['arm']]['short']})")
        for ln in x["lines"]:
            emit("text", f"    {'✗' if ln['struck'] else '✓'} {ln['index'] or '(line not found)'}")
        emit("text", "    summary: " + "\n             ".join(_wrap(x["summary"] or "(none)", 90)))
    out = dict(jd=j["id"], title=j["title"], file=j["file"], changes=v["changes"], final=v["final"], views=views)
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
        n_docs += len((cli.run("proj", "doc", "list", "--project", proj["id"], "--page-size", "100",
                               scoped=True, echo=False) or {}).get("items") or [])
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundaries, {n_proj} project(s) ({n_docs} document(s)) and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()
    shutil.rmtree(OUT / "accepted", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "jd", "cleanup"],
                    help="run = full demo (default); jd <de|csm|wol> = one JD's tracked changes and its edited lines "
                         "as the index holds them in both projects; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for jd: de (Senior Data Engineer), csm (Customer Success Manager), "
                                            "wol (Warehouse Operations Lead)")
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
    if args.command == "jd":
        if not args.args:
            die("usage: python3 demo.py jd <de|csm|wol>")
        banner(2, 2, f"JD {args.args[0]} — the tracked changes, and the edited lines in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py jd de`; `python3 demo.py cleanup` removes the demo data.")


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

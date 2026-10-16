#!/usr/bin/env python3
"""letter-comment-memory — the partner's comments reach the index; which clause each one is on does not.

Larkspur & Vance CPAs (a fictional accounting firm) keeps its past engagement letters in shared memory, so that an
engagement-letter assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "which
clause did Halloway refuse last year?" before the next letter goes out. The letters are Word documents, and the
partner's review notes are Word comments: "Halloway refused this outright in the 2025 renewal. Do not offer it to
them again." The comment doesn't name the clause. In Word it doesn't have to — the balloon points at it.

MemoryLake's index reads the comment text as `Commented [RV2]: …` and puts it where the balloon sits on the
rendered page, not next to the clause it is on: in these letters five of the eight comments come out after the
*next* clause. MemoryRouter attaches that text, and the assistant names the clause the comment happens to sit
under. The same files, with each comment written into the letter right after the clause it is on (a 30-line
function in this script, run before upload), come back right.

The demo shows:
  · each letter's comments, read from the .docx itself, with the clause each one is on;
  · where each comment sits in the index text (`search --types document`), in both projects;
  · ten questions a partner asks (eight about comments, two about the letter nobody commented on) through two
    boundaries — the letters as saved, the same letters with comments anchored — each asked several times; a
    checker call (Claude copies out what it was given) shows what MemoryRouter attached.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (80 calls per run, roughly 40,000 tokens).
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

PREFIX = "mlu-lcm"  # memorylake-usecases / letter-comment-memory


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
ARMS = ("saved", "anchored")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "letters.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def letter_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for x in d["letters"]:
        if key in (x["id"], x["file"]):
            return x
    die(f"no letter {key!r}; one of: {', '.join(x['id'] for x in d['letters'])}")


def lib_name(d: dict, x: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{x['file']}{d['arms'][arm]['suffix']}"


def letter_by_name(d: dict, name: str) -> tuple[dict | None, str]:
    for x in d["letters"]:
        for arm in ARMS:
            if name == lib_name(d, x, arm):
                return x, arm
    return None, ""


def clause_title(x: dict, n: int) -> str:
    return x["clauses"][n - 1][0] if 0 < n <= len(x["clauses"]) else "?"


def clause_name(x: dict, n: int | None) -> str:
    return f"clause {n} ({clause_title(x, n)})" if n else "no clause"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


# --------------------------------------------------------------------------- .docx: read the comments, anchor them

W_TEXT = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.S)
W_PARA = re.compile(r"<w:p\b(?:[^>]*[^/])?>.*?</w:p>", re.S)
CLAUSE = re.compile(r"^(\d{1,2})\.\s+([^.]+)\.")


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _text(xml: str) -> str:
    return _unescape("".join(W_TEXT.findall(xml)))


def read_comments(blob: bytes) -> list[dict]:
    """Every comment in a .docx, in document order: who wrote it, its text, and the paragraph it is on (its clause
    number and title when the paragraph starts "N. Title.") — read from the file itself."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    if "word/comments.xml" not in z.namelist():
        return []
    body = {}
    for m in re.finditer(r"<w:comment\b([^>]*)>(.*?)</w:comment>", z.read("word/comments.xml").decode("utf-8"), re.S):
        attr = dict(re.findall(r'w:(\w+)="([^"]*)"', m.group(1)))
        body[attr.get("id")] = dict(author=_unescape(attr.get("author", "")), initials=attr.get("initials", ""),
                                    text=_text(m.group(2)))
    out = []
    for p in W_PARA.findall(z.read("word/document.xml").decode("utf-8")):
        text = _text(p)
        m = CLAUSE.match(text)
        for k in re.findall(r'<w:commentReference\b[^>]*w:id="([^"]+)"', p):
            c = body.get(k)
            if c:
                out.append(dict(c, on=int(m.group(1)) if m else None, title=m.group(2) if m else "", para=text))
    for n, c in enumerate(out, 1):
        c["mark"] = f"{c['initials']}{n}"  # how Word (and the index) numbers them: initials + order in the letter
    return out


def anchor_comments(blob: bytes) -> bytes:
    """The fix, before upload: write each comment into the letter as its own paragraph right after the paragraph it
    is on, naming that clause — "[Partner comment on clause 5 (Limitation of liability), R. Vance: …]" — and drop
    the comment markers and the comments part. The letter text itself is unchanged."""
    comments = {c["para"]: [] for c in read_comments(blob)}
    for c in read_comments(blob):
        comments[c["para"]].append(c)
    src = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename == "word/comments.xml":
                continue
            data = src.read(info).decode("utf-8") if info.filename.endswith((".xml", ".rels")) else src.read(info)
            if info.filename == "word/document.xml":
                def expand(m: re.Match) -> str:
                    p = m.group(0)
                    p = re.sub(r"<w:comment(?:RangeStart|RangeEnd)\b[^>]*/>", "", p)
                    p = re.sub(r"<w:r>(?:(?!</w:r>).)*?<w:commentReference\b[^>]*/></w:r>", "", p, flags=re.S)
                    notes = comments.get(_text(m.group(0)), [])
                    for c in notes:
                        where = f"clause {c['on']} ({c['title']})" if c["on"] else "the paragraph above"
                        note_ = f"[Partner comment on {where}, {c['author']}: {c['text']}]"
                        p += f'<w:p><w:r><w:t xml:space="preserve">{escape_xml(note_)}</w:t></w:r></w:p>'
                    return p
                data = W_PARA.sub(expand, data)
            elif info.filename == "word/_rels/document.xml.rels":
                data = re.sub(r'<Relationship\b[^>]*Target="comments\.xml"[^>]*/>', "", data)
            elif info.filename == "[Content_Types].xml":
                data = re.sub(r'<Override\b[^>]*PartName="/word/comments\.xml"[^>]*/>', "", data)
            dst.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def anchored_path(x: dict) -> Path:
    return OUT / "anchored" / (x["file"] + ".anchored.docx")


def file_for(d: dict, x: dict, arm: str) -> Path:
    return DATA / (x["file"] + ".docx") if arm == "saved" else anchored_path(x)


# --------------------------------------------------------------------------- where each comment sits in index text

SAVED_NOTE = re.compile(r"^Commented \[([A-Za-z]+\d+)\]:\s*(.*)")
ANCHORED_NOTE = re.compile(r"^\[Partner comment on clause (\d+)[^:]*:\s*(.*?)\]?$")


def landings(text: str, comments: list[dict]) -> list[dict]:
    """For each comment of the letter: does the text hold it, and after which clause does it sit? Index text comes
    in overlapping chunks, in any order, and a chunk can start mid-sentence: a comment seen once after a clause is
    not counted again, and one seen before any clause line (the chunk began below the clause) is replaced by a
    later sighting that has one."""
    after, found = None, {}
    for raw in text.splitlines():
        ln = raw.strip()
        if ln.lower().startswith("engagement letter"):
            after = None  # the letter's title: a new pass over the page starts here
            continue
        m = CLAUSE.match(ln)
        if m:
            after = int(m.group(1))
            continue
        m = SAVED_NOTE.match(ln) or ANCHORED_NOTE.match(ln)
        if not m:
            continue
        body = norm(m.group(2))
        for k, c in enumerate(comments):
            if not body or not norm(c["text"]).startswith(body[:40]):
                continue
            if k not in found or found[k]["after"] is None:
                found[k] = dict(after=after, line=ln)
            break
    return [dict(mark=c["mark"], on=c["on"], text=c["text"], held=k in found,
                 after=found[k]["after"] if k in found else None, line=found[k]["line"] if k in found else "")
            for k, c in enumerate(comments)]


# --------------------------------------------------------------------------- grading

REFUSAL = re.compile(r"\b(?:don't|do not|doesn't|does not) have (?:any |specific )?(?:information|details)|"
                     r"\bwas(?:n't| not) (?:given|provided)|\bno information\b|\bnot (?:mentioned|specified)\b", re.I)
NUMBERED = re.compile(r"\b(?:clause|section)\s*#?\s*(\d{1,2})\b|\b(\d{1,2})\.\s+(?=[A-Z])", re.I)


def clauses_named(x: dict, reply: str) -> set[int]:
    """The clause numbers a reply names ("Clause 5", "5. Limitation of liability"); failing those, the clause
    titles it names."""
    got = {int(a or b) for a, b in NUMBERED.findall(reply)}
    got = {n for n in got if 0 < n <= len(x["clauses"])}
    if got:
        return got
    low = norm(reply)
    return {n for n, (title, _) in enumerate(x["clauses"], 1) if re.search(r"\b" + re.escape(norm(title)) + r"\b", low)}


def grade(q: dict, reply: str, sits_after: int | None) -> dict:
    """right = names the clause the comment is on, and only it; under = names only the clause the comment sits
    under in the as-saved index (when that is another clause); other = names some other single clause;
    mixed = several; none = no clause, or says it was not given it."""
    x = letter_of(load_data(), q["letter"])
    named = clauses_named(x, reply)
    if not named or (REFUSAL.search(reply) and q["on"] not in named):
        return dict(verdict="none", named=sorted(named))
    if named == {q["on"]}:
        v = "right"
    elif len(named) > 1:
        v = "mixed"
    elif sits_after and sits_after != q["on"] and named == {sits_after}:
        v = "under"
    else:
        v = "other"
    return dict(verdict=v, named=sorted(named))


MARK = {"right": "✓", "under": "✗", "other": "✗", "mixed": "◐", "none": "·", None: "–"}


def truth_text(d: dict, q: dict) -> str:
    x = letter_of(d, q["letter"])
    c = next((c for c in read_comments((DATA / (x["file"] + ".docx")).read_bytes()) if c["on"] == q["on"]), None)
    if c:
        return f"{clause_name(x, q['on'])} — {c['author']}'s comment {c['mark']}: “{c['text']}”"
    return f"{clause_name(x, q['on'])} — {x['clauses'][q['on'] - 1][1]}"


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
        note("an earlier run left the letter library behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['firm']} — {d['library']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Engagement letters: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Engagement-letter assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the letter library

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


def letter_view(d: dict, x: dict) -> dict:
    """One letter: its clauses, and the comments read back from the .docx itself."""
    comments = read_comments((DATA / (x["file"] + ".docx")).read_bytes())
    return dict(id=x["id"], file=x["file"], client=x["client"], title=x["title"],
                clauses=[dict(n=n, title=t, text=b) for n, (t, b) in enumerate(x["clauses"], 1)],
                comments=[{k: c[k] for k in ("mark", "author", "text", "on", "title")} for c in comments],
                names={a: lib_name(d, x, a) for a in ARMS})


def show_library(d: dict) -> list[dict]:
    views = []
    for x in d["letters"]:
        v = letter_view(d, x)
        views.append(v)
        head = f"\n  {x['file']}.docx — {x['client']}, {len(x['clauses'])} clauses"
        if not v["comments"]:
            emit("text", head + "\n    no comments (the control)")
            continue
        body = [f"    {len(v['comments'])} comments by {v['comments'][0]['author']} in Word's comment pane — "
                "the balloon points at the clause, the text doesn't name it:"]
        body += [f"      {c['mark']}  on clause {c['on']} ({c['title']}):  “{c['text']}”" for c in v["comments"]]
        emit("text", head + "\n" + "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [x["file"] + ".docx" for x in d["letters"] if not (DATA / (x["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_letters.py`")
    views = show_library(d)
    (OUT / "anchored").mkdir(parents=True, exist_ok=True)
    for x in d["letters"]:
        before = read_comments((DATA / (x["file"] + ".docx")).read_bytes())
        fixed = anchor_comments((DATA / (x["file"] + ".docx")).read_bytes())
        if read_comments(fixed):
            die(f"anchor_comments left comments in {x['file']}.docx")
        body = zipfile.ZipFile(io.BytesIO(fixed)).read("word/document.xml").decode("utf-8")
        if any(escape_xml(c["text"]) not in body for c in before):
            die(f"anchor_comments lost a comment's text in {x['file']}.docx")
        anchored_path(x).write_bytes(fixed)
    note("the fix, before upload: anchor_comments() writes each comment in after the clause it is on, naming it "
         f"(“[Partner comment on clause 5 (…), R. Vance: …]”) → {(OUT / 'anchored').relative_to(HERE)}/*.anchored.docx")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for x in d["letters"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, x, arm)), "--name", lib_name(d, x, arm),
                                      "--on-conflict", "overwrite", echo=k in (0, len(d["letters"])))["item_id"])
            k += 1
    note(f"{k} uploads: the three letters as saved, and the same three with comments anchored; each project imports its own")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all letters indexed in {int(time.time() - t0)} s")
    emit("memory", "", letters=views, arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the comments in the index

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


def letter_in_index(cli: CLI, world: dict, x: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, x, arm)
    comments = read_comments((DATA / (x["file"] + ".docx")).read_bytes())
    docs = [y for y in index_docs(cli, world["project"][arm], f"{x['client']} engagement letter", echo) if y["name"] == name]
    if not docs:
        return dict(letter=x["id"], arm=arm, name=name, found=False, comments=[], summary="")
    return dict(letter=x["id"], arm=arm, name=name, found=True, comments=landings(docs[0]["text"], comments),
                summary=docs[0]["summary"])


def placed(c: dict) -> str:
    return "–" if not c["held"] else "✓" if c["after"] == c["on"] else "✗"


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    jobs = [(x, arm) for x in d["letters"] for arm in ARMS]
    with ThreadPoolExecutor(6) as pool:
        views = list(pool.map(lambda xa: letter_in_index(cli, world, xa[0], xa[1], xa == jobs[0]), jobs))
    if not any(v["found"] for v in views):
        die("search did not return the letters yet; run again in a minute")
    per = {a: dict(letters=0, comments=0, held=0, in_place=0, displaced=0) for a in ARMS}
    for v in views:
        p = per[v["arm"]]
        p["letters"] += v["found"]
        for c in v["comments"]:
            p["comments"] += 1
            p["held"] += c["held"]
            p["in_place"] += c["held"] and c["after"] == c["on"]
            p["displaced"] += c["held"] and c["after"] != c["on"]
    for x in d["letters"]:
        vs = {v["arm"]: v for v in views if v["letter"] == x["id"]}
        if not x["comments"]:
            emit("text", f"\n  {x['file']} — no comments; the index holds the same text in both projects")
            continue
        out = [f"\n  {x['file']} — each comment, and the clause it sits after in the index text"]
        for k, c in enumerate(vs["saved"]["comments"] if vs.get("saved", {}).get("found") else []):
            out.append(f"    {c['mark']}  on {clause_name(x, c['on'])}")
            for arm in ARMS:
                v = vs.get(arm)
                y = v["comments"][k] if v and v["found"] else None
                where = "(not in the index text)" if not y or not y["held"] else (
                    f"sits after {clause_name(x, y['after'])}" + ("" if y["after"] == y["on"] else "  ← the next clause down"
                                                                   if (y["after"] or 0) == (y["on"] or 0) + 1 else "  ← another clause"))
                out.append(f"        {placed(y) if y else '–'} {d['arms'][arm]['short']:<18} {where}")
        s = vs.get("saved")
        if s and s["found"]:
            shown = next((c["line"] for c in s["comments"] if c["held"] and c["after"] != c["on"]), "")
            if shown:
                out.append(f"    the index text reads: “{shown[:110]}” — no clause number, no anchor")
        emit("text", "\n".join(out))
    emit("text", "\n  where the comments sit in the index, per project:\n" + "\n".join(
        f"    {d['arms'][a]['short']:<18} comments in the index text {per[a]['held']}/{per[a]['comments']}"
        f" · after the clause they are on {per[a]['in_place']} · after another clause {per[a]['displaced']}"
        for a in ARMS))
    note("MemoryRouter attaches the page text; the checker in steps 5-6 copies out what it did attach")
    emit("index", "", letters=views, per=per)
    return dict(letters=views, per=per)


def sits_after(index: dict, q: dict) -> int | None:
    """Where the comment this question is about sits in the as-saved index (None for the uncommented letter)."""
    v = next((v for v in index["letters"] if v["letter"] == q["letter"] and v["arm"] == "saved"), None)
    c = next((c for c in (v or {}).get("comments", []) if c["on"] == q["on"]), None)
    return c["after"] if c and c["held"] else None


def group_of(index: dict, q: dict) -> str:
    x = letter_of(load_data(), q["letter"])
    if not x["comments"]:
        return "control"
    after = sits_after(index, q)
    return "unknown" if after is None else "in place" if after == q["on"] else "displaced"


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


def answer(cli: CLI, q: dict, boundary: str, under: int | None, echo: bool = False) -> dict:
    d = load_data()
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "160", "--temperature", "0",
                            "--system", d["system"], "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, named=[])
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(q, text, under))


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
    """Each entry the checker copied out, by its "(file)" label: which letter, and where each of its comments sits
    in that text. "NONE" is kept apart as "declined": it is not proof that nothing was attached."""
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
        x, arm = letter_by_name(d, m.group(1))
        cs = landings(body, read_comments((DATA / (x["file"] + ".docx")).read_bytes())) if x else []
        entries.append(dict(file=m.group(1), letter=x["id"] if x else "?", arm=arm, comments=cs, text=body[:1500]))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def attached_comment(c: dict, q: dict) -> dict | None:
    """In a checker copy: the entries of the question's letter, and the question's comment among them."""
    hit = [e for e in c["entries"] if e["letter"] == q["letter"]]
    for e in hit:
        for y in e["comments"]:
            if y["on"] == q["on"] and y["held"]:
                return dict(letter=True, after=y["after"])
    return dict(letter=True, after=None) if hit else None


def attached_state(checks: list[dict], q: dict, x: dict) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if not c:
        return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"
    if not c["entries"]:
        return "nothing attached"
    names = sorted({e["file"][len(PREFIX) + 1:] for e in c["entries"]})
    a = attached_comment(c, q)
    if not a:
        return "attached " + ", ".join(names) + f" — not {x['file']}"
    if not x["comments"]:
        return "attached " + ", ".join(names)
    if a["after"] is None:
        return "attached " + ", ".join(names) + " — without the comment"
    return "attached " + ", ".join(names) + f" — the comment sits after {clause_name(x, a['after'])}"


def worst(answers: list[dict]) -> dict:
    order = {"under": 0, "other": 1, "mixed": 2, "none": 3, "right": 4, None: 5}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str, index: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        x = letter_of(d, q["letter"])
        under = sits_after(index, q)
        group = group_of(index, q)
        emit("text", f"\n  {q['id']}  {q['say']}\n      the letter: {truth_text(d, q)}"
             + (f"\n      as-saved index: the comment sits after {clause_name(x, under)}" if group == "displaced" else ""))
        first = answer(cli, q, boundary, under, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary, under) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        state = attached_state(checks, q, x)
        emit("text", "      checker: " + "\n               ".join(_wrap(state, 96)))
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], letter=q["letter"], on=q["on"], group=group, under=under, arm=arm,
                         answers=answers, checks=checks))
        emit("asked", "", q=q["id"], letter=q["letter"], on=q["on"], group=group, under=under, arm=arm, say=q["say"],
             truth=truth_text(d, q), attached=state, declined=any(c.get("declined") for c in checks),
             answers=[dict(verdict=a["verdict"], named=a["named"], reply=a["reply"], tokens=a["prompt_tokens"])
                      for a in answers])
    return rows


GROUPS = ("displaced", "in place", "control")


def tally(rows: list[dict]) -> dict:
    """Answers by verdict, per group of questions: comments the as-saved index puts after another clause
    (displaced), comments it puts after their own clause (in place), and the uncommented letter (control). And,
    over every readable checker copy, whether the question's letter was attached and where its comment sat."""
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "under", "other", "mixed", "none")}
        out[g]["answers"] = len(ans)
    need = att = held = right_place = 0
    for r in rows:
        if r["group"] == "control":
            continue
        for c in r["checks"]:
            if not c["readable"]:
                continue
            need += 1
            a = attached_comment(c, dict(letter=r["letter"], on=r["on"]))
            if a:
                att += 1
                if a["after"] is not None:
                    held += 1
                    right_place += a["after"] == r["on"]
    chk = [c for r in rows for c in r["checks"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         need=need, attached=att, held=held, right_place=right_place)
    return out


def tally_line(t: dict) -> str:
    c, dsp, inp, ctl = t["checks"], t["displaced"], t["in place"], t["control"]
    return (f"displaced comments: {dsp['right']}/{dsp['answers']} right · {dsp['under']} named the clause above the comment"
            f" · {dsp['other'] + dsp['mixed'] + dsp['none']} other  ·  in-place comments: {inp['right']}/{inp['answers']}"
            f"  ·  uncommented letter: {ctl['right']}/{ctl['answers']}"
            f"  ·  checker: the letter attached {c['attached']}/{c['need']}, with the comment {c['held']}/{c['attached']},"
            f" after its own clause {c['right_place']}/{c['held']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the right clause: one line per distinct reply, "the clause above" first."""
    order = ["under", "other", "mixed", "none"]
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
    banner(2, TOTAL, "Set up — a project for each copy of the letter library, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The letter library — three Word files, two of them with the partner's comments")
    store(cli, world)

    banner(4, TOTAL, "The comments in the index — next to which clause?")
    index = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, arm in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) copies out what it was given")
        got = ask_all(cli, world, arm, index)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        text = (f"  {d['arms'][arm]['short']:<18} in the index: comments after the clause they are on "
                f"{p['in_place']}/{p['comments']} · after another clause {p['displaced']}/{p['comments']}")
        if arm in tallies:
            text += f"\n  {'':<18} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant named instead of the clause the comment is on "
                     "(✗ the clause above the comment in the index, or another · ◐ several · · none):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], letters=index["letters"], tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and checker reading: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         index=index["per"], instead=said[:10])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_comments() -> dict:
    """Offline: each letter's comments as the .docx holds them, and what anchor_comments() writes instead."""
    d = load_data()
    out = []
    for x in d["letters"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        cs = read_comments(blob)
        emit("text", f"\n  {x['file']}.docx — {x['client']}: " + (f"{len(cs)} comment(s)" if cs else "no comments"))
        for c in cs:
            emit("text", f"    {c['mark']}  on clause {c['on']} ({c['title']}):  “{c['text']}”")
        fixed = zipfile.ZipFile(io.BytesIO(anchor_comments(blob))).read("word/document.xml").decode("utf-8")
        notes = [ln for ln in (_text(p) for p in W_PARA.findall(fixed)) if ln.startswith("[Partner comment")]
        if notes:
            emit("text", "    anchored copy writes, after each clause:\n" + "\n".join(f"      {n}" for n in notes))
        out.append(dict(letter=x["id"], comments=cs, anchored=notes))
    emit("comments", "", letters=out)
    return dict(letters=out)


def show_one(cli: CLI, key: str) -> dict:
    """One letter: its comments, and where each one sits in the index text of both projects."""
    d = load_data()
    x = letter_of(d, key)
    world = load_world(cli)
    v = letter_view(d, x)
    emit("text", f"\n  {x['file']}.docx — {x['client']}\n  " + (
        f"comments by {v['comments'][0]['author']}:\n" + "\n".join(
            f"    {c['mark']}  on clause {c['on']} ({c['title']}): “{c['text']}”" for c in v["comments"])
        if v["comments"] else "no comments"))
    views = [letter_in_index(cli, world, x, arm, echo=k == 0) for k, arm in enumerate(ARMS)]
    for y in views:
        emit("text", f"\n  {y['name'][len(PREFIX) + 1:]}  ({d['arms'][y['arm']]['short']})")
        if not y["found"]:
            emit("text", "    (not returned by search yet)")
        for c in y["comments"]:
            where = f"sits after {clause_name(x, c['after'])}" if c["held"] else "not in the index text"
            emit("text", f"    {placed(c)} {c['mark']} (on clause {c['on']}) {where}")
    out = dict(letter=x["id"], client=x["client"], file=x["file"], comments=v["comments"], clauses=v["clauses"],
               views=views)
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
    shutil.rmtree(OUT / "anchored", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "letter", "comments", "cleanup"],
                    help="run = full demo (default); letter <hd|bb|fl> = one letter's comments and where each sits "
                         "in the index of both projects; comments = each letter's comments, read from the .docx "
                         "(offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for letter: hd (Halloway Dental), bb (Brightwater Bakery), "
                                            "fl (Fenwick & Lowe)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "comments":
        banner(1, 1, "Comments — what the Word files hold (offline)")
        show_comments()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "letter":
        if not args.args:
            die("usage: python3 demo.py letter <hd|bb|fl>")
        banner(2, 2, f"Letter {args.args[0]} — its comments, and where each sits in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py letter hd`; `python3 demo.py cleanup` removes the demo data.")


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

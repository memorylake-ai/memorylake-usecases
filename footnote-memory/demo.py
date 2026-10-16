#!/usr/bin/env python3
"""footnote-memory — the footnotes reach the index; which sentence each one qualifies does not.

Kestrel (a fictional data-platform company) keeps its platform team's Word documents — the service limits and the
release policy — in shared memory, so that a platform assistant (an ordinary model call through MemoryRouter with
`--boundary`) can answer an engineer's "what's the export rate limit for our EU-West key?" without anyone digging
through the docs. The exceptions in those documents are Word footnotes, written the way people write footnotes:
"¹ 200 for keys on the EU-West cluster, until the storage migration finishes." The footnote doesn't say what it is
200 *of*. On the page it doesn't have to — the superscript 1 sits on the sentence it qualifies.

MemoryLake's index reads the page as rendered: the sentence keeps a bare `$ ^{1} $`, and the footnote text goes to
the foot of the page — under the heading of whatever section happens to be last on that page. MemoryRouter attaches
both, and the assistant answers the sentence's 1,000. The same files, with each footnote written in right after
the sentence it qualifies and naming its section (a 30-line function in this script, run before upload), come
back right.

The demo shows:
  · each document's footnotes, read from the .docx itself, with the section and sentence each one qualifies;
  · where each footnote sits in the index text (`search --types document`), in both projects;
  · ten questions an engineer asks (seven answered by a footnote, three by a section without one) through two
    boundaries — the documents as saved, the same documents with footnotes inlined — each asked several times; a
    checker call (Claude copies out what it was given) shows what MemoryRouter attached.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (80 calls per run, roughly 60,000 tokens).
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

PREFIX = "mlu-ftn"  # memorylake-usecases / footnote-memory


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
ARMS = ("saved", "inlined")
EMPTY_TOKENS = 150                # a prompt this small is the system prompt and the question: nothing was attached


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "docs.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def doc_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for x in d["docs"]:
        if key in (x["id"], x["file"]):
            return x
    die(f"no document {key!r}; one of: {', '.join(x['id'] for x in d['docs'])}")


def lib_name(d: dict, x: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{x['file']}{d['arms'][arm]['suffix']}"


def doc_by_name(d: dict, name: str) -> tuple[dict | None, str]:
    for x in d["docs"]:
        for arm in ARMS:
            if name == lib_name(d, x, arm):
                return x, arm
    return None, ""


def section_title(x: dict, n: int | None) -> str:
    return x["sections"][n - 1][0] if n and 0 < n <= len(x["sections"]) else "?"


def section_name(x: dict, n: int | None) -> str:
    return f"§{n} {section_title(x, n)}" if n else "no section"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


# --------------------------------------------------------------------------- .docx: read the footnotes, inline them

W_TEXT = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.S)
W_PARA = re.compile(r"<w:p\b(?:[^>]*[^/])?>.*?</w:p>", re.S)
W_REF = re.compile(r'<w:footnoteReference\b[^>]*w:id="([^"]+)"[^>]*/>')
HEADING = re.compile(r"^(\d{1,2})\.\s+([A-Z][^.]{1,60})$")


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _text(xml: str) -> str:
    return _unescape("".join(W_TEXT.findall(xml)))


def read_footnotes(blob: bytes) -> list[dict]:
    """Every footnote in a .docx, in document order: its number, its text, and the section and sentence its
    reference sits on — read from the file itself."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    if "word/footnotes.xml" not in z.namelist():
        return []
    body = {}
    for m in re.finditer(r"<w:footnote\b([^>]*)>(.*?)</w:footnote>", z.read("word/footnotes.xml").decode("utf-8"), re.S):
        attr = dict(re.findall(r'w:(\w+)="([^"]*)"', m.group(1)))
        if attr.get("type") in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        body[attr.get("id")] = _text(m.group(2)).strip()
    out, on, title = [], None, ""
    for p in W_PARA.findall(z.read("word/document.xml").decode("utf-8")):
        text = _text(p).strip()
        m = HEADING.match(text)
        if m and not W_REF.search(p):
            on, title = int(m.group(1)), m.group(2).strip()
            continue
        for ref in W_REF.finditer(p):
            if ref.group(1) in body:
                before = _text(p[:ref.start()]).strip()
                sentence = re.split(r"(?<=[.!?])\s+", before)[-1] if before else ""
                out.append(dict(n=len(out) + 1, text=body[ref.group(1)], on=on, title=title, sentence=sentence))
    return out


def inline_label(f: dict) -> str:
    return f"[Footnote {f['n']} to section {f['on']} ({f['title']}): {f['text']}]"


def inline_footnotes(blob: bytes) -> bytes:
    """The fix, before upload: write each footnote into the text right where its reference is, naming the section
    it qualifies — "[Footnote 1 to section 2 (Batch export rate): 200 for keys on the EU-West cluster, …]" — and
    drop the footnotes part. Every other word of the document is unchanged."""
    notes = {str(f["n"]): f for f in read_footnotes(blob)}
    src = zipfile.ZipFile(io.BytesIO(blob))
    ids = {}  # footnote id in the file -> its number in document order
    doc_xml = src.read("word/document.xml").decode("utf-8")
    for k, ref in enumerate(W_REF.finditer(doc_xml), 1):
        ids[ref.group(1)] = str(k)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename == "word/footnotes.xml":
                continue
            data = src.read(info).decode("utf-8") if info.filename.endswith((".xml", ".rels")) else src.read(info)
            if info.filename == "word/document.xml":
                def expand(m: re.Match) -> str:
                    f = notes.get(ids.get(W_REF.search(m.group(0)).group(1), ""))
                    if not f:
                        return ""
                    return f'<w:r><w:t xml:space="preserve"> {escape_xml(inline_label(f))}</w:t></w:r>'
                data = re.sub(r"<w:r\b[^>]*>(?:(?!</w:r>).)*?<w:footnoteReference\b[^>]*/></w:r>", expand, data, flags=re.S)
            elif info.filename == "word/_rels/document.xml.rels":
                data = re.sub(r'<Relationship\b[^>]*Target="footnotes\.xml"[^>]*/>', "", data)
            elif info.filename == "[Content_Types].xml":
                data = re.sub(r'<Override\b[^>]*PartName="/word/footnotes\.xml"[^>]*/>', "", data)
            dst.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inlined_path(x: dict) -> Path:
    return OUT / "inlined" / (x["file"] + ".inlined.docx")


def file_for(d: dict, x: dict, arm: str) -> Path:
    return DATA / (x["file"] + ".docx") if arm == "saved" else inlined_path(x)


def footnotes_of(x: dict) -> list[dict]:
    return read_footnotes((DATA / (x["file"] + ".docx")).read_bytes())


# --------------------------------------------------------------------------- where each footnote sits in index text

FOOT_LINE = re.compile(r"^\$\s*\^\{(\d+)\}\s*\$?\s*(.*)$")     # "$ ^{1} $  200 for keys on …" at the foot of a page
MARKER = re.compile(r"\$\s*\^\{(\d+)\}\s*\$")                    # "… per API key. $ ^{1} $  The exporter …"
INLINE = re.compile(r"\[Footnote (\d+) to section (\d+) \([^)]*\):\s*(.*?)\]")


def an(s: str) -> str:
    """Letters and digits only: the index writes some symbols as LaTeX, so match on words."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def key(s: str, words: int = 8) -> str:
    return " ".join(an(s).split()[:words])


def section_after(x: dict, chunk: str, words_key: str) -> int | None:
    """The section whose text comes right before a footnote line in a chunk (skipping other footnote lines)."""
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    at = next((i for i, ln in enumerate(lines) if words_key in an(ln)), None)
    if at is None:
        return None
    for ln in reversed(lines[:at]):
        if FOOT_LINE.match(ln):
            continue
        m = HEADING.match(ln)
        if m:
            return int(m.group(1))
        tail = " ".join(an(MARKER.sub(" ", ln)).split()[-6:])
        for n, s in enumerate(x["sections"], 1):
            if tail and tail in an(" ".join(t for t in (s[1], *s[3:]) if t)):
                return n
        return None
    return None


def placements(chunks: list[str], x: dict) -> list[dict]:
    """For each footnote of the document, over a set of index chunks (what search returns, or what MemoryRouter
    attached): is the sentence it qualifies there, are the footnote's words there, and are the two in the same
    chunk? Where the words are in another chunk: which section's text comes right before them."""
    out = []
    for f in footnotes_of(x):
        sk, fk = key(f["sentence"]), key(f["text"], 6)
        with_s = [i for i, c in enumerate(chunks) if sk in an(c)]
        with_f = [i for i, c in enumerate(chunks) if fk in an(c)]
        together = sorted(set(with_s) & set(with_f))
        how, line, after = "", "", None
        for i in (together or with_f)[:1]:
            for ln in chunks[i].splitlines():
                if fk in an(ln):
                    line = ln.strip()
                    how = "inline" if "[Footnote" in ln else "foot" if FOOT_LINE.match(line) else "text"
                    break
            if how != "inline":
                after = section_after(x, chunks[i], fk)
        mark = any(re.search(r"\$\s*\^\{" + str(f["n"]) + r"\}", chunks[i]) for i in with_s)
        out.append(dict(n=f["n"], on=f["on"], text=f["text"], sentence=bool(with_s), held=bool(with_f),
                        together=bool(together), how=how, after=after, line=line, mark=mark))
    return out


def where(x: dict, y: dict | None) -> str:
    if not y or not y["held"]:
        return "(the footnote's words are not in the index text search returned)"
    if y["together"]:
        return "in the same chunk as its sentence, right after it" if y["how"] == "inline" else (
            "in the same chunk as its sentence, at the foot of the page")
    after = f", after the text of {section_name(x, y['after'])}" if y["after"] else ""
    return f"in another chunk — at the foot of the page{after}  ← apart from its sentence"


# --------------------------------------------------------------------------- grading

REFUSAL = re.compile(r"\b(?:don't|do not|doesn't|does not) have (?:any |specific )?(?:information|details)|"
                     r"\bwas(?:n't| not) (?:given|provided)|\bno information\b|\bnot (?:mentioned|specified)\b", re.I)


def grade(q: dict, reply: str, tokens: int = 0) -> dict:
    """right = gives the footnote's value (or, for a control question, the section's) and not the section's own
    value; body = gives the section's value the footnote overrides; mixed = both; none = neither, or says it was
    not given it. empty = nothing was attached to that call (the prompt was only the system prompt and question)."""
    r = bool(re.search(q["right"], reply, re.I | re.M))
    b = bool(q.get("body") and re.search(q["body"], reply, re.I | re.M))
    if r and b:
        v = "mixed"
    elif r:
        v = "right"
    elif b:
        v = "body"
    else:
        v = "none"
    if v != "right" and REFUSAL.search(reply) and not b:
        v = "none"
    return dict(verdict=v, empty=bool(tokens) and tokens < EMPTY_TOKENS)


MARK = {"right": "✓", "body": "✗", "mixed": "◐", "none": "·", None: "–"}


def truth_text(d: dict, q: dict) -> str:
    x = doc_of(d, q["doc"])
    f = next((f for f in footnotes_of(x) if f["on"] == q["on"]), None)
    if f and q["kind"] == "footnote":
        return f"{section_name(x, q['on'])}: “{f['sentence']}”{sup(f['n'])} — footnote {f['n']}: “{f['text']}”"
    return f"{section_name(x, q['on'])}: “{x['sections'][q['on'] - 1][1].split('. ')[0]}.”  (no footnote)"


SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def sup(n: int) -> str:
    return str(n).translate(SUPERSCRIPT)


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
        note("an earlier run left the platform docs behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['company']} platform docs ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Platform docs: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Platform assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the documents

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


def doc_view(d: dict, x: dict) -> dict:
    """One document: its sections, and the footnotes read back from the .docx itself."""
    return dict(id=x["id"], file=x["file"], title=x["title"],
                sections=[dict(n=n, title=s[0], text=" ".join(t for t in (s[1], *s[3:]) if t)) for n, s in
                          enumerate(x["sections"], 1)],
                footnotes=[{k: f[k] for k in ("n", "text", "on", "title", "sentence")} for f in footnotes_of(x)],
                inline=[inline_label(f) for f in footnotes_of(x)],
                names={a: lib_name(d, x, a) for a in ARMS})


def show_docs(d: dict) -> list[dict]:
    views = []
    for x in d["docs"]:
        v = doc_view(d, x)
        views.append(v)
        body = [f"\n  {x['file']}.docx — {len(x['sections'])} sections, {len(v['footnotes'])} footnotes. "
                "In Word each one sits under a superscript on the sentence it qualifies:"]
        for f in v["footnotes"]:
            body.append(f"    {sup(f['n'])}  on §{f['on']} {f['title']}: “{f['sentence']}”{sup(f['n'])}")
            body.append(f"         footnote {f['n']}: “{f['text']}”")
        emit("text", "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [x["file"] + ".docx" for x in d["docs"] if not (DATA / (x["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_docs.py`")
    views = show_docs(d)
    (OUT / "inlined").mkdir(parents=True, exist_ok=True)
    for x in d["docs"]:
        before = footnotes_of(x)
        fixed = inline_footnotes((DATA / (x["file"] + ".docx")).read_bytes())
        if read_footnotes(fixed):
            die(f"inline_footnotes left footnotes in {x['file']}.docx")
        body = _text(zipfile.ZipFile(io.BytesIO(fixed)).read("word/document.xml").decode("utf-8"))
        if any(inline_label(f) not in body for f in before):
            die(f"inline_footnotes lost a footnote in {x['file']}.docx")
        inlined_path(x).write_bytes(fixed)
    f0 = footnotes_of(d["docs"][0])[0]
    note(f"the fix, before upload: inline_footnotes() writes each footnote in after the sentence it qualifies, naming "
         f"its section (“{inline_label(f0)[:60]}…]”) → {(OUT / 'inlined').relative_to(HERE)}/*.inlined.docx")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for x in d["docs"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, x, arm)), "--name", lib_name(d, x, arm),
                                      "--on-conflict", "overwrite", echo=k in (0, len(d["docs"])))["item_id"])
            k += 1
    note(f"{k} uploads: the two documents as saved, and the same two with footnotes inlined; each project imports its own")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all documents indexed in {int(time.time() - t0)} s")
    emit("memory", "", docs=views, arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the footnotes in the index

def index_chunks(cli: CLI, project: str, x: dict, name: str, echo: bool = False) -> tuple[list[str], str]:
    """The index chunks of one document that `search --types document` returns: one search for the title, one for
    each footnoted sentence and one for each footnote's words, so every chunk that holds either comes back."""
    notes = footnotes_of(x)
    queries = [x["title"]] + [f["sentence"] for f in notes] + [f["text"] for f in notes]

    def one(k_q):
        k, q = k_q
        return cli.run("search", q, "--projects", project, "--types", "document", "--top-k", "6",
                       scoped=True, echo=echo and k == 0) or {}

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(one, enumerate(queries)))
    texts, summary = [], ""
    for res in results:
        for doc in res.get("documents") or []:
            if (doc.get("document_name") or doc.get("file_name") or "") != name:
                continue
            summary = summary or (doc.get("document_summary") or "").strip()
            for it in doc.get("items") or []:
                for ch in (it.get("highlight") or {}).get("chunks") or []:
                    t = (ch.get("text") or "").strip()
                    if t and t not in texts:
                        texts.append(t)
    texts = [t for t in texts if not any(t != u and t in u for u in texts)]  # drop chunks contained in another
    return texts, summary


def doc_in_index(cli: CLI, world: dict, x: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, x, arm)
    chunks, summary = index_chunks(cli, world["project"][arm], x, name, echo)
    if not chunks:
        return dict(doc=x["id"], arm=arm, name=name, found=False, footnotes=[], chunks=0, summary="")
    return dict(doc=x["id"], arm=arm, name=name, found=True, footnotes=placements(chunks, x), chunks=len(chunks),
                summary=summary)


def placed(y: dict | None) -> str:
    return "–" if not y or not y["held"] else "✓" if y["together"] else "✗"


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    jobs = [(x, arm) for x in d["docs"] for arm in ARMS]
    with ThreadPoolExecutor(2) as pool:
        views = list(pool.map(lambda xa: doc_in_index(cli, world, xa[0], xa[1], xa == jobs[0]), jobs))
    if not any(v["found"] for v in views):
        die("search did not return the documents yet; run again in a minute")
    per = {a: dict(docs=0, footnotes=0, held=0, together=0, apart=0, marks=0) for a in ARMS}
    for v in views:
        p = per[v["arm"]]
        p["docs"] += v["found"]
        for y in v["footnotes"]:
            p["footnotes"] += 1
            p["held"] += y["held"]
            p["together"] += y["together"]
            p["apart"] += y["held"] and not y["together"]
            p["marks"] += y["mark"]
    for x in d["docs"]:
        vs = {v["arm"]: v for v in views if v["doc"] == x["id"]}
        out = [f"\n  {x['file']} — each footnote, and where its words sit in the index: "
               + ", ".join(f"{d['arms'][a]['short']} {vs[a]['chunks']} chunks" for a in ARMS if a in vs)]
        for k, f in enumerate(footnotes_of(x)):
            out.append(f"    {sup(f['n'])}  on §{f['on']} {f['title']}: “{f['text'][:70]}”")
            for arm in ARMS:
                v = vs.get(arm)
                y = v["footnotes"][k] if v and v["found"] else None
                extra = f"\n{'':<30}(its sentence keeps only “$ ^{{{f['n']}}} $”)" if (
                    arm == "saved" and y and y["mark"] and not y["together"]) else ""
                out.append(f"        {placed(y)} {d['arms'][arm]['short']:<18} {where(x, y)}{extra}")
        s = vs.get("saved")
        if s and s["found"]:
            shown = next((y["line"] for y in s["footnotes"] if y["held"] and not y["together"]), "")
            if shown:
                out.append(f"    the footnote's chunk reads: “{shown[:110]}” — no section, no subject")
        emit("text", "\n".join(out))
    emit("text", "\n  where the footnotes sit in the index, per project:\n" + "\n".join(
        f"    {d['arms'][a]['short']:<18} footnotes found {per[a]['held']}/{per[a]['footnotes']}"
        f" · in the same chunk as their sentence {per[a]['together']} · in another chunk {per[a]['apart']}"
        for a in ARMS))
    note("MemoryRouter attaches chunks; the checker in steps 5-6 copies out which ones it did attach")
    emit("index", "", docs=views, per=per)
    return dict(docs=views, per=per)


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
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "160", "--temperature", "0",
                            "--system", d["system"], "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, empty=False)
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(q, text, u.get("prompt_tokens", 0)))


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
CHECK_EMPTY = 260  # the checker's own system prompt and the question: nothing was attached


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out, by its "(file)" label: which document, and its text. "NONE" with a
    prompt no bigger than the checker's own instructions is "nothing attached"; "NONE" with more is kept apart as
    "declined": it is not proof that nothing was attached."""
    d = load_data()
    parts = text.split("```")
    raw = text[:8000]
    if len(parts) < 3:
        return dict(readable=False, entries=[], declined=False, raw=raw, tokens=tokens)
    block = parts[1]
    if block.strip() == "NONE":
        empty = bool(tokens) and tokens < CHECK_EMPTY
        return dict(readable=empty, entries=[], declined=not empty, raw=raw, tokens=tokens)
    marks = list(LABEL.finditer(block))
    entries = []
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)].strip()
        x, arm = doc_by_name(d, m.group(1))
        entries.append(dict(file=m.group(1), doc=x["id"] if x else "?", arm=arm, text=body))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def attached_footnote(c: dict, q: dict) -> dict | None:
    """In a checker copy: the entries of the question's document — is the section's sentence there, is the
    footnote there, and are they in the same entry."""
    d = load_data()
    x = doc_of(d, q["doc"])
    hit = [e["text"] for e in c["entries"] if e["doc"] == q["doc"]]
    if not hit:
        return None
    f = next((f for f in footnotes_of(x) if f["on"] == q["on"]), None)
    if not f or q.get("kind") == "control":
        rule = key(x["sections"][q["on"] - 1][1])
        return dict(doc=True, rule=any(rule in an(t) for t in hit), foot=False, inline=False, after=None)
    y = placements(hit, x)[f["n"] - 1]
    return dict(doc=True, rule=y["sentence"], foot=y["held"], inline=y["held"] and y["how"] == "inline",
                after=y["after"])


def attached_state(checks: list[dict], q: dict, x: dict) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if not c:
        return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"
    if not c["entries"]:
        return "nothing attached"
    names = sorted({e["file"][len(PREFIX) + 1:] for e in c["entries"]})
    a = attached_footnote(c, q)
    head = "attached " + ", ".join(names)
    if not a:
        return head + f" — not {x['file']}"
    sec = f"§{q['on']}'s sentence " + ("in" if a["rule"] else "not in")
    if q["kind"] == "control":
        return head + f" — {sec}"
    if not a["foot"]:
        return head + f" — {sec}, its footnote not"
    if a["inline"]:
        return head + f" — {sec}, its footnote right after it"
    return head + f" — {sec}, its footnote at the foot of the page" + (
        f", after the text of {section_name(x, a['after'])}" if a["after"] else "")


def worst(answers: list[dict]) -> dict:
    order = {"body": 0, "mixed": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: (a.get("empty", False), order[a["verdict"]]))[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        x = doc_of(d, q["doc"])
        emit("text", f"\n  {q['id']}  {q['say']}\n      the document: {truth_text(d, q)}")
        first = answer(cli, q, boundary, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        state = attached_state(checks, q, x)
        emit("text", "      checker: " + "\n               ".join(_wrap(state, 96)))
        pick = worst(answers)
        emit("text", f"      answers {' '.join(('∅' if a['empty'] else '') + MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], doc=q["doc"], on=q["on"], kind=q["kind"], arm=arm, answers=answers, checks=checks))
        emit("asked", "", q=q["id"], doc=q["doc"], on=q["on"], qkind=q["kind"], arm=arm, say=q["say"],
             truth=truth_text(d, q), attached=state, declined=any(c.get("declined") for c in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"], empty=a["empty"])
                      for a in answers])
    return rows


KINDS = ("footnote", "control")


def tally(rows: list[dict]) -> dict:
    """Answers by verdict, per kind of question (answered by a footnote / by a section without one), counting only
    calls that had something attached; calls that had nothing attached are counted apart. And, over every readable
    checker copy of a footnote question: the sentence attached, the footnote attached, both in one chunk."""
    out: dict = {}
    for g in KINDS:
        got = [a for r in rows if r["kind"] == g for a in r["answers"] if a["verdict"]]
        ans = [a for a in got if not a["empty"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "body", "mixed", "none")}
        out[g]["answers"] = len(ans)
        out[g]["empty"] = len(got) - len(ans)
    need = nothing = rule = foot = inline = rule_only = 0
    for r in rows:
        if r["kind"] != "footnote":
            continue
        for c in r["checks"]:
            if not c["readable"]:
                continue
            need += 1
            a = attached_footnote(c, dict(doc=r["doc"], on=r["on"], kind=r["kind"]))
            if not c["entries"]:
                nothing += 1
            if a:
                rule += a["rule"]
                foot += a["foot"]
                inline += a["inline"]
                rule_only += a["rule"] and not a["foot"]
    chk = [c for r in rows for c in r["checks"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         need=need, nothing=nothing, rule=rule, foot=foot, inline=inline, rule_only=rule_only)
    return out


def tally_line(t: dict) -> str:
    c, fn, ctl = t["checks"], t["footnote"], t["control"]
    empty = fn["empty"] + ctl["empty"]
    return (f"footnote questions: {fn['right']}/{fn['answers']} right · {fn['body']} gave the section's own value"
            f" · {fn['mixed'] + fn['none']} other  ·  sections without a footnote: {ctl['right']}/{ctl['answers']}"
            + (f"  ·  {empty} call(s) had nothing attached (not counted)" if empty else "")
            + f"  ·  checker on footnote questions: the sentence attached {c['rule']}/{c['need']},"
            f" without its footnote {c['rule_only']} · footnote at the foot of the page {c['foot'] - c['inline']}"
            f" · footnote right after its sentence {c['inline']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the footnote's value: one line per distinct reply, "the section's own
    value" first; replies from calls with nothing attached last."""
    order = ["body", "mixed", "none"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text in seen:
                continue
            seen.add(text)
            tag = " (nothing attached)" if a["empty"] else ""
            found.append(((a["empty"], order.index(a["verdict"])),
                          f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}]{tag} “{text[:150]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 7
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for each copy of the platform docs, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The platform docs — two Word files whose exceptions are footnotes")
    store(cli, world)

    banner(4, TOTAL, "The footnotes in the index — next to which sentence?")
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
        text = (f"  {d['arms'][arm]['short']:<18} in the index: footnotes in the same chunk as their sentence "
                f"{p['together']}/{p['footnotes']} · in another chunk {p['apart']}/{p['footnotes']}")
        if arm in tallies:
            text += f"\n  {'':<18} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the footnote's value "
                     "(✗ the section's own value · ◐ both · · neither):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], docs=index["docs"], tokens=tok,
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

def show_footnotes() -> dict:
    """Offline: each document's footnotes as the .docx holds them, and what inline_footnotes() writes instead."""
    d = load_data()
    out = []
    for x in d["docs"]:
        fs = footnotes_of(x)
        emit("text", f"\n  {x['file']}.docx — {len(fs)} footnote(s)")
        for f in fs:
            emit("text", f"    {sup(f['n'])}  on §{f['on']} {f['title']}: “{f['sentence']}”{sup(f['n'])}\n"
                         f"         footnote {f['n']}: “{f['text']}”")
        emit("text", "    the inlined copy writes, right after each of those sentences:\n"
             + "\n".join(f"      {inline_label(f)}" for f in fs))
        out.append(dict(doc=x["id"], footnotes=fs, inline=[inline_label(f) for f in fs]))
    emit("footnotes", "", docs=out)
    return dict(docs=out)


def show_one(cli: CLI, which: str) -> dict:
    """One document: its footnotes, and where each one sits in the index of both projects."""
    d = load_data()
    x = doc_of(d, which)
    world = load_world(cli)
    v = doc_view(d, x)
    emit("text", f"\n  {x['file']}.docx\n" + "\n".join(
        f"    {sup(f['n'])}  on §{f['on']} {f['title']}: “{f['text']}”" for f in v["footnotes"]))
    with ThreadPoolExecutor(2) as pool:
        views = list(pool.map(lambda ka: doc_in_index(cli, world, x, ka[1], echo=ka[0] == 0), enumerate(ARMS)))
    for y in views:
        emit("text", f"\n  {y['name'][len(PREFIX) + 1:]}  ({d['arms'][y['arm']]['short']}, {y['chunks']} chunks)")
        if not y["found"]:
            emit("text", "    (not returned by search yet)")
        for f in y["footnotes"]:
            emit("text", f"    {placed(f)} footnote {f['n']} (on §{f['on']}) {where(x, f)}")
    out = dict(doc=x["id"], file=x["file"], title=x["title"], footnotes=v["footnotes"], sections=v["sections"], views=views)
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
    shutil.rmtree(OUT / "inlined", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "doc", "footnotes", "cleanup"],
                    help="run = full demo (default); doc <limits|release> = one document's footnotes and where each "
                         "sits in the index of both projects; footnotes = each document's footnotes, read from the "
                         ".docx (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for doc: limits (service limits) or release (release policy)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "footnotes":
        banner(1, 1, "Footnotes — what the Word files hold (offline)")
        show_footnotes()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "doc":
        if not args.args:
            die("usage: python3 demo.py doc <limits|release>")
        banner(2, 2, f"Document {args.args[0]} — its footnotes, and where each sits in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py doc limits`; `python3 demo.py cleanup` removes the demo data.")


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

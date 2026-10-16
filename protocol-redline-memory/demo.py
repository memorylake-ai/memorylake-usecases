#!/usr/bin/env python3
"""protocol-redline-memory — a protocol amendment marked up by hand: the strikethrough doesn't reach the index.

Northfield Clinical Research Unit (a fictional research site) keeps its study documents in shared memory, so that a
study assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "what's the minimum
eGFR at screening?" while a coordinator pre-screens a patient. The sponsor sends each protocol amendment as a Word
file marked up by hand, the way many sponsors do: deleted text struck through, new text bold and underlined. Not
Word's Track Changes — the strikethrough is just a font setting on the old words.

MemoryLake's index reads the page without the font: "Age 18 to 6518 to 75 years", "week 12 24" — and on two lines
the new value is not in the index text at all, only the struck-through old one ("within 90 days"). MemoryRouter
attaches that text, and the assistant answers with criteria the amendment deleted. The same files with the struck
text removed before upload (a 20-line function in this script) come back right. The usual fix for marked-up Word
files, accepting tracked changes, finds nothing to accept here.

The demo shows:
  · each amendment's edits, read from the .docx itself (struck-through run → bold-underlined run);
  · that accepting tracked changes changes nothing (there are none), and what apply_redline() leaves;
  · each edited line in the index text (`search --types document`), in both projects: both values, the old one
    only, or the new one only — and what the index's own document summary says;
  · nine questions a coordinator asks (seven about edited lines, two controls) through two boundaries — the files
    as saved, the same files with the strikethrough applied — each asked several times.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (63 calls per run, roughly 19,000 tokens).
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

PREFIX = "mlu-prl"  # memorylake-usecases / protocol-redline-memory


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




MODEL = "gpt-4o-mini"  # answers: router chat (OpenAI Chat Completions)
RUNS = 3               # answers per question per boundary
ARMS = ("saved", "applied")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "protocols.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def doc_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for x in d["docs"]:
        if key in (x["id"], x["file"], x["study"].lower()):
            return x
    die(f"no document {key!r}; one of: {', '.join(x['id'] for x in d['docs'])}")


def lib_name(d: dict, x: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{x['file']}{d['arms'][arm]['suffix']}"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def flat(parts: list, keep: str) -> str:
    """A line's text. keep="old": as before the amendment · "new": as amended · "both": every word on the page."""
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif keep == "both" or (keep == "old") == (p[0] == "del"):
            out.append(p[1])
    return " ".join("".join(out).split())


def edits_of(x: dict) -> list[dict]:
    """Each struck-through → new pair in protocols.json, with the line before and after."""
    out = []
    for label, parts in x["lines"]:
        old = [p[1] for p in parts if not isinstance(p, str) and p[0] == "del"]
        new = [p[1] for p in parts if not isinstance(p, str) and p[0] == "ins"]
        if old or new:
            out.append(dict(label=label, old=" ".join(old), new=" ".join(new),
                            before=flat(parts, "old"), after=flat(parts, "new")))
    return out


def line_of(x: dict, label: str) -> str:
    return next((f"{a}: {flat(p, 'new')}" for a, p in x["lines"] if a == label), "?")


# --------------------------------------------------------------------------- .docx: read the hand markup, apply it

W_TEXT = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.S)
W_PARA = re.compile(r"<w:p\b(?:[^>]*[^/])?>.*?</w:p>", re.S)
W_RUN = re.compile(r"<w:r\b(?:[^>]*[^/])?>.*?</w:r>", re.S)
ON = r'(?:\s+w:val="(?:true|1|on)")?\s*/>'
STRUCK = re.compile(r"<w:(?:strike|dstrike)" + ON)
BOLD = re.compile(r"<w:b" + ON)
UNDER = re.compile(r'<w:u\s+w:val="(?!none")[^"]*"\s*/>')


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _text(xml: str) -> str:
    return _unescape("".join(W_TEXT.findall(xml)))


def body_xml(blob: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")


def paragraphs(blob: bytes) -> list[str]:
    return [" ".join(_text(p).split()) for p in W_PARA.findall(body_xml(blob))]


def read_marks(blob: bytes) -> list[dict]:
    """The hand markup, read from the file itself: on each paragraph, the struck-through runs (deleted) and the
    runs in bold + underline (new). Only the font says which is which."""
    out = []
    for p in W_PARA.findall(body_xml(blob)):
        struck, added = [], []
        for r in W_RUN.findall(p):
            m = re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S)
            props = m.group(1) if m else ""
            if STRUCK.search(props):
                struck.append(_text(r))
            elif BOLD.search(props) and UNDER.search(props):
                added.append(_text(r))
        if struck or added:
            line = " ".join(_text(p).split())
            out.append(dict(label=line.split(":")[0].strip(), old=" ".join(struck), new=" ".join(added), line=line))
    return out


def tracked_changes(blob: bytes) -> int:
    """Word's own revision marks (Track Changes): <w:ins>, <w:del>, <w:moveFrom>, <w:moveTo>."""
    return len(re.findall(r"<w:(?:ins|del|moveFrom|moveTo)\b", body_xml(blob)))


def accept_changes(blob: bytes) -> bytes:
    """What Word's Review › Accept All Changes does to the text (the fix in jd-redline-memory): drop every deletion,
    keep every insertion's words. Here as the control: on a hand-marked file it has nothing to act on."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                x = data.decode("utf-8")
                x = re.sub(r"<w:(del|moveFrom)\b[^>]*?/>", "", x)
                x = re.sub(r"<w:(del|moveFrom)\b[^>]*>.*?</w:\1>", "", x, flags=re.S)
                x = re.sub(r"</?w:(ins|moveTo)\b[^>]*>", "", x)
                data = x.encode("utf-8")
            dst.writestr(info, data)
    return buf.getvalue()


def apply_redline(blob: bytes) -> bytes:
    """The fix, before upload: remove every struck-through run (the words the amendment deleted), and take the bold
    and underline off the new words, so the file reads like the amended protocol. Nothing else changes."""
    def one(m: re.Match) -> str:
        r = m.group(0)
        props = re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S)
        if not props:
            return r
        if STRUCK.search(props.group(1)):
            return ""
        if BOLD.search(props.group(1)) and UNDER.search(props.group(1)):
            return r.replace(props.group(0), "<w:rPr>" + UNDER.sub("", BOLD.sub("", props.group(1))) + "</w:rPr>")
        return r
    src = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "word/document.xml":
                data = W_RUN.sub(one, data.decode("utf-8")).encode("utf-8")
            dst.writestr(info, data)
    return buf.getvalue()


def applied_path(x: dict) -> Path:
    return OUT / "current" / (x["file"] + ".current.docx")


def file_for(d: dict, x: dict, arm: str) -> Path:
    return DATA / (x["file"] + ".docx") if arm == "saved" else applied_path(x)


# --------------------------------------------------------------------------- an edit in index text

def has(token: str, text: str) -> bool:
    """Is this value in the text? Numbers as whole numbers (45 is not in 145 or 4.5), words as a phrase."""
    t = norm(token)
    if re.fullmatch(r"\d+", t):
        return bool(re.search(rf"(?<![\d.]){t}(?![\d])", norm(text)))
    return t in norm(text)


def find_line(text: str, label: str) -> str:
    want = norm(label + ":")
    for raw in text.splitlines():
        if want in norm(raw):
            return " ".join(raw.split())
    return ""


def kept(line: str, e: dict) -> str:
    """both · old only · new only · neither — which of the two values the index line holds."""
    if not line:
        return "line missing"
    tail = line.split(":", 1)[1] if ":" in line else line
    o, n = has(e["old"], tail), has(e["new"], tail)
    return "both" if o and n else "old only" if o else "new only" if n else "neither"


KEPT = ("both", "old only", "new only", "neither", "line missing")


# --------------------------------------------------------------------------- grading

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)


def first_clause(reply: str) -> str:
    """The value the assistant gives: the "Answer:" line (or the first sentence), cut at the first full stop, ';'
    or a because/as clause, so a sentence that goes on to mention the other value does not count twice."""
    m = ANSWER.search(reply)
    s = m.group(1) if m else reply.strip().split("\n")[0]
    s = re.split(r"(?<=[a-z0-9%)])\.(?:\s|$)|;|\s+(?:because|since|as per|as stated|which)\b", s, maxsplit=1)[0]
    return s


def grade(q: dict, reply: str, tokens: int, empty: int) -> dict:
    """empty = MemoryRouter attached nothing (same prompt tokens as the call without a boundary): the reply is the
    model on its own, not counted either way. right = the amended value and not the deleted one; stale = the
    deleted value and not the amended one; mixed = both; other = neither."""
    if tokens and empty and tokens <= empty:
        return dict(verdict="empty", clause=first_clause(reply))
    c = norm(first_clause(reply))
    r = bool(re.search(q["right"], c))
    w = bool(q.get("wrong") and re.search(q["wrong"], c))
    return dict(verdict="right" if r and not w else "stale" if w and not r else "mixed" if r and w else "other", clause=c)


MARK = {"right": "✓", "stale": "✗", "mixed": "◐", "other": "·", "empty": "∅", None: "–"}


def group_of(d: dict, q: dict) -> str:
    x = doc_of(d, q["doc"])
    return "edited" if any(e["label"] == q["on"] for e in edits_of(x)) else "control"


def truth_text(d: dict, q: dict) -> str:
    x = doc_of(d, q["doc"])
    e = next((e for e in edits_of(x) if e["label"] == q["on"]), None)
    if not e:
        return f"{x['file']}.docx, no markup on this line: “{line_of(x, q['on'])}”"
    return (f"{x['file']}.docx, amended: “{q['on']}: {e['after']}”\n"
            f"      struck through on the same line: “{e['old']}” (the value before the amendment)")


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
        note("an earlier run left the study library behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['site']} — {d['library']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Study documents: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Study assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the study library

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
    """One document: its lines, and the hand markup read back from the .docx itself."""
    marks = read_marks((DATA / (x["file"] + ".docx")).read_bytes())
    return dict(id=x["id"], file=x["file"], study=x["study"], title=x["title"], saved=x["saved"],
                lines=[dict(label=a, parts=p) for a, p in x["lines"]],
                marks=[dict(label=m["label"], old=m["old"], new=m["new"]) for m in marks],
                names={a: lib_name(d, x, a) for a in ARMS})


def show_library(d: dict) -> list[dict]:
    views = []
    for x in d["docs"]:
        v = doc_view(d, x)
        views.append(v)
        head = f"\n  {x['file']}.docx — {x['title']}, from {d['sponsor']}, {x['saved']}"
        if not v["marks"]:
            emit("text", head + "\n    no markup (the control)")
            continue
        body = [f"    {len(v['marks'])} edits marked by hand — struck-through run (deleted) → bold + underlined run (new):"]
        for m in v["marks"]:
            body.append(f"      {m['label']:<22} struck “{m['old']}” → new “{m['new']}”")
        emit("text", head + "\n" + "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [x["file"] + ".docx" for x in d["docs"] if not (DATA / (x["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_protocols.py`")
    views = show_library(d)
    (OUT / "current").mkdir(parents=True, exist_ok=True)
    tracked = struck = 0
    for x in d["docs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        tracked += tracked_changes(blob)
        if paragraphs(accept_changes(blob)) != paragraphs(blob):
            die(f"accept_changes changed the text of {x['file']}.docx")
        fixed = apply_redline(blob)
        if read_marks(fixed):
            die(f"apply_redline left markup in {x['file']}.docx")
        want = [("" if a == "Legend" else f"{a}: ") + flat(p, "new") for a, p in x["lines"]]
        got = paragraphs(fixed)[2:]
        if [norm(g) for g in got] != [norm(w) for w in want]:
            die(f"apply_redline did not leave the amended text in {x['file']}.docx:\n  {got}\n  {want}")
        struck += len(read_marks(blob))
        applied_path(x).write_bytes(fixed)
    note(f"Word's Track Changes in these files: {tracked}. accept_changes() — Review › Accept All Changes, the fix "
         "for tracked edits — leaves every line exactly as it was: the strikethrough is only a font setting")
    note(f"the fix, before upload: apply_redline() removes the {struck} struck-through runs and takes the bold and "
         f"underline off the new words; each line now reads as amended → {(OUT / 'current').relative_to(HERE)}/*.current.docx")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for x in d["docs"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, x, arm)), "--name", lib_name(d, x, arm),
                                      "--on-conflict", "overwrite", echo=k in (0, len(d["docs"])))["item_id"])
            k += 1
    note(f"{k} uploads: the three documents as saved, and the same three with the strikethrough applied; each project "
         "imports its own")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all documents indexed in {int(time.time() - t0)} s")
    emit("memory", "", docs=views, tracked=tracked, struck=struck,
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the edits in the index

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
            texts.append(((it.get("text") or "") + "\n" + chunks).strip())
        out.append(dict(name=name, summary=(doc.get("document_summary") or "").strip(), text="\n".join(texts).strip()))
    return out


def doc_in_index(cli: CLI, world: dict, x: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, x, arm)
    docs = [y for y in index_docs(cli, world["project"][arm], f"{x['title']} criteria dosing endpoint", echo)
            if y["name"] == name]
    if not docs:
        return dict(doc=x["id"], arm=arm, name=name, found=False, edits=[], summary="", in_summary=[], text="")
    text, summary = docs[0]["text"], docs[0]["summary"]
    edits = []
    for e in edits_of(x):
        line = find_line(text, e["label"])
        edits.append(dict(label=e["label"], old=e["old"], new=e["new"], line=line, kept=kept(line, e)))
    in_summary = [dict(label=e["label"], value=e["old"]) for e in edits_of(x)
                  if has(e["old"], summary) and not has(e["new"], summary) and not re.fullmatch(r"\d", e["old"])]
    return dict(doc=x["id"], arm=arm, name=name, found=True, edits=edits, summary=summary, in_summary=in_summary,
                text=text[:3000])


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    jobs = [(x, arm) for x in d["docs"] for arm in ARMS]
    for attempt in range(6):
        with ThreadPoolExecutor(6) as pool:
            views = list(pool.map(lambda xa: doc_in_index(cli, world, xa[0], xa[1], xa == jobs[0] and attempt == 0), jobs))
        if all(v["found"] for v in views):
            break
        time.sleep(10)
    if not any(v["found"] for v in views):
        die("search did not return the documents yet; run again in a minute")
    per = {a: {k: 0 for k in KEPT} for a in ARMS}
    for v in views:
        for e in v["edits"]:
            per[v["arm"]][e["kept"]] += 1
    for x in d["docs"]:
        vs = {v["arm"]: v for v in views if v["doc"] == x["id"]}
        if not edits_of(x):
            emit("text", f"\n  {x['file']} — no markup; the index holds the same text in both projects")
            continue
        out = [f"\n  {x['file']} — each edited line as the index text holds it"]
        for k, e in enumerate(edits_of(x)):
            out.append(f"    {e['label']:<22} struck “{e['old']}” → new “{e['new']}”")
            for arm in ARMS:
                v = vs.get(arm)
                y = v["edits"][k] if v and v["found"] else None
                shown = f"“{y['line'][:120]}”" if y and y["line"] else "(line not found)"
                out.append(f"        {d['arms'][arm]['short']:<15} {y['kept'] if y else '–':<9} {shown}")
        s = vs.get("saved")
        if s and s["found"] and s["summary"]:
            out.append("    the index's own summary of the as-saved file: “" + s["summary"][:300] + "”")
            if s["in_summary"]:
                out.append("      … states the deleted value: " + ", ".join(f"{m['label']} {m['value']}" for m in s["in_summary"]))
        emit("text", "\n".join(out))
    for arm in ARMS:
        p = per[arm]
        emit("text", f"\n  {d['arms'][arm]['short']:<15} edited lines in the index: both values {p['both']} · old only "
                     f"{p['old only']} · new only {p['new only']}" + (f" · neither {p['neither']}" if p["neither"] else "")
             + (f" · line not found {p['line missing']}" if p["line missing"] else ""))
    note("no strikethrough, no “deleted”, nothing marks which value is gone: the index reads the words on the page "
         "without their font. MemoryRouter attaches this text")
    emit("index", "", docs=views, per=per)
    return dict(docs=views, per=per)


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
    args = ["chat", q["say"], "--model", MODEL, "--max-tokens", "120", "--temperature", "0", "--system", d["system"]]
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
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, clause="")
    text, tokens = got
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(q, text, tokens, empty))


def worst(answers: list[dict]) -> dict:
    order = {"stale": 0, "mixed": 1, "other": 2, "empty": 3, "right": 4, None: 5}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        group = group_of(d, q)
        emit("text", f"\n  {q['id']}  {q['say']}\n      {truth_text(d, q)}")
        first = answer(cli, q, boundary, base.get(q["id"], 0), echo=n == 0)
        with ThreadPoolExecutor(RUNS) as pool:
            more = [pool.submit(answer, cli, q, boundary, base.get(q["id"], 0)) for _ in range(RUNS - 1)]
            answers = [first] + [f.result() for f in more]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], doc=q["doc"], on=q["on"], group=group, arm=arm, answers=answers))
        emit("asked", "", q=q["id"], doc=q["doc"], on=q["on"], group=group, arm=arm, say=q["say"], truth=truth_text(d, q),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


GROUPS = ("edited", "control")
VERDICTS = ("right", "stale", "mixed", "other", "empty")


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
    return out


def tally_line(t: dict) -> str:
    e, c = t["edited"], t["control"]
    graded = lambda g: g["answers"] - g["empty"]
    return (f"edited lines: {e['right']}/{graded(e)} right · {e['stale']} gave the deleted value · {e['mixed']} gave both"
            f" · {e['other']} other" + (f" · {e['empty']} ∅" if e["empty"] else "")
            + f"  ·  controls: {c['right']}/{graded(c)}" + (f" · ∅ {c['empty']}" if c["empty"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the amended value: one line per distinct reply, deleted value first."""
    order = ["stale", "mixed", "other"]
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
    banner(2, TOTAL, "Set up — a project for each copy of the study library, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The study library — two amendments marked up by hand, one clean manual")
    store(cli, world)

    banner(4, TOTAL, "The edits in the index — which value is left?")
    index = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    base: dict = {}
    for step, arm in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        if not base:
            note("first, each question once without a boundary: those prompt tokens are the system prompt and the "
                 "question alone, so a boundary call with the same count had nothing attached (∅)")
            base = baselines(cli)
            if not cli.router_ok:  # type: ignore[attr-defined]
                break
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. "
             "✓ the amended value · ✗ the deleted value · ◐ both · · neither · ∅ nothing attached")
        got = ask_all(cli, world, arm, base)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        text = (f"  {d['arms'][arm]['short']:<15} in the index: edited lines with both values {p['both']} · "
                f"the deleted value only {p['old only']} · the new value only {p['new only']}")
        if arm in tallies:
            text += f"\n  {'':<15} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the amended value "
                     "(✗ the deleted value · ◐ both · · neither):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], docs=index["docs"], baselines=base, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         index=index["per"], instead=said[:10])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_redlines() -> dict:
    """Offline: each document's hand markup as the .docx holds it, what accept_changes() finds, what apply_redline()
    leaves."""
    d = load_data()
    out = []
    for x in d["docs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        marks = read_marks(blob)
        emit("text", f"\n  {x['file']}.docx — {x['title']}: " + (f"{len(marks)} edit(s) marked by hand" if marks else "no markup"))
        for m in marks:
            emit("text", f"    {m['label']:<22} struck “{m['old']}” → bold + underlined “{m['new']}”")
        emit("text", f"    Track Changes revisions: {tracked_changes(blob)} · accept_changes() changes "
                     f"{sum(a != b for a, b in zip(paragraphs(accept_changes(blob)), paragraphs(blob)))} line(s)")
        fixed = paragraphs(apply_redline(blob))
        for m in marks:
            emit("text", "    apply_redline() → “" + next((p for p in fixed if p.startswith(m["label"] + ":")), "?") + "”")
        out.append(dict(doc=x["id"], marks=marks, tracked=tracked_changes(blob), applied=fixed))
    emit("redlines", "", docs=out)
    return dict(docs=out)


def show_one(cli: CLI, key: str) -> dict:
    """One document: its hand-marked edits, and each edited line in the index text of both projects."""
    d = load_data()
    x = doc_of(d, key)
    world = load_world(cli)
    v = doc_view(d, x)
    emit("text", f"\n  {x['file']}.docx — {x['title']}\n  " + (
        "edits marked by hand:\n" + "\n".join(f"    {m['label']:<22} struck “{m['old']}” → new “{m['new']}”" for m in v["marks"])
        if v["marks"] else "no markup"))
    views = [doc_in_index(cli, world, x, arm, echo=k == 0) for k, arm in enumerate(ARMS)]
    for y in views:
        emit("text", f"\n  {y['name'][len(PREFIX) + 1:]}  ({d['arms'][y['arm']]['short']})")
        if not y["found"]:
            emit("text", "    (not returned by search yet)")
            continue
        for e in y["edits"]:
            emit("text", f"    {e['label']:<22} {e['kept']:<9} " + (f"“{e['line'][:96]}”" if e["line"] else "(line not found)"))
        if y["summary"]:
            emit("text", "    summary: “" + y["summary"][:300] + "”")
    out = dict(doc=x["id"], study=x["study"], file=x["file"], marks=v["marks"], views=views)
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
    shutil.rmtree(OUT / "current", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "doc", "redlines", "cleanup"],
                    help="run = full demo (default); doc <klv|orv|pharm> = one document's hand-marked edits and each "
                         "edited line in the index of both projects; redlines = each document's markup read from the "
                         ".docx, and what accept_changes() and apply_redline() do with it (offline); cleanup = delete "
                         "everything")
    ap.add_argument("args", nargs="*", help="for doc: klv (KLV-301 protocol), orv (ORV-118 protocol), pharm (pharmacy manual)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "redlines":
        banner(1, 1, "Redlines — what the Word files hold (offline)")
        show_redlines()
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
            die("usage: python3 demo.py doc <klv|orv|pharm>")
        banner(2, 2, f"Document {args.args[0]} — its edits, and each edited line in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py doc klv`; `python3 demo.py cleanup` removes the demo data.")


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

#!/usr/bin/env python3
"""claim-highlight-memory — "yellow = not cleared to publish": the highlighter doesn't reach the index.

Kestrel Outdoor (a fictional outdoor-gear brand) keeps its launch fact sheets in shared memory, so that the content
team's assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "what brightness can I
state in the launch post?" while a writer drafts. Product marketing marks each claim's status with the highlighter:
yellow = still waiting for test-lab or legal sign-off, do not publish; green = wording cleared by legal. The legend
at the top of each sheet says so.

MemoryLake's index reads the words without their colour: the yellow claims read exactly like the verified ones,
and the index's own summary of each sheet states them as facts. The assistant hands the writer "1,200 lumens",
"IPX7", "100% recycled nylon". Writing the status into the text ("[UNVERIFIED - not cleared to publish: …]") is
not enough either: where a line holds nothing but the unverified claim, the assistant still quotes it. What holds
is taking the unverified number out of the file before upload and saying it isn't cleared yet.

The demo shows:
  · each sheet's highlights, read from the .docx itself (colour → claim);
  · the two fixes, applied before upload: tag_highlights() and withhold_highlights();
  · each highlighted line in the index text (`search --types document`) of all three projects, and what the
    index's own summary of the as-saved sheet says;
  · eleven questions a writer asks (seven about yellow claims, two about green ones, two about plain lines)
    through three boundaries — as saved, tagged, withheld — each asked several times.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (110 calls per run, roughly 34,000 tokens).
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

PREFIX = "mlu-chl"  # memorylake-usecases / claim-highlight-memory


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
ARMS = ("saved", "tagged", "withheld")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "factsheets.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def doc_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for x in d["docs"]:
        if key in (x["id"], x["file"], x["product"].lower()):
            return x
    die(f"no document {key!r}; one of: {', '.join(x['id'] for x in d['docs'])}")


def lib_name(d: dict, x: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{x['file']}{d['arms'][arm]['suffix']}"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def flat(d: dict, parts: list, arm: str) -> str:
    """A line's text in one of the three files: as saved (every word, colour lost or not), tagged, withheld."""
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif p[0] != "yellow" or arm == "saved":
            out.append(p[1])
        elif arm == "tagged":
            out.append(d["tag_text"].format(p[1]))
        else:
            out.append(d["withheld_text"])
    return " ".join("".join(out).split())


def marks_of(x: dict) -> list[dict]:
    """Each highlighted claim in factsheets.json: line, colour, the claim, the short key that identifies it."""
    return [dict(label=label, colour=p[0], claim=p[1], key=p[2])
            for label, parts in x["lines"] for p in parts if not isinstance(p, str)]


def line_of(d: dict, x: dict, label: str, arm: str = "saved") -> str:
    return next((f"{a}: {flat(d, p, arm)}" for a, p in x["lines"] if a == label), "?")


# --------------------------------------------------------------------------- .docx: read the highlights, apply a fix

W_TEXT = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.S)
W_PARA = re.compile(r"<w:p\b(?:[^>]*[^/])?>.*?</w:p>", re.S)
W_RUN = re.compile(r"<w:r\b(?:[^>]*[^/])?>.*?</w:r>", re.S)
HIGHLIGHT = re.compile(r'<w:highlight\s+w:val="([a-zA-Z]+)"\s*/>')


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(xml: str) -> str:
    return _unescape("".join(W_TEXT.findall(xml)))


def body_xml(blob: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")


def paragraphs(blob: bytes) -> list[str]:
    return [" ".join(_text(p).split()) for p in W_PARA.findall(body_xml(blob))]


def _colour(run_xml: str) -> str:
    m = re.search(r"<w:rPr>(.*?)</w:rPr>", run_xml, re.S)
    h = HIGHLIGHT.search(m.group(1)) if m else None
    return h.group(1).lower() if h and h.group(1).lower() != "none" else ""


def read_highlights(blob: bytes) -> list[dict]:
    """The highlighter marks, read from the file itself: each highlighted run, its colour, and the line it is on.
    Nothing but the run property says which claims are cleared."""
    out = []
    for p in W_PARA.findall(body_xml(blob)):
        line = " ".join(_text(p).split())
        for r in W_RUN.findall(p):
            c = _colour(r)
            if c:
                out.append(dict(label=line.split(":")[0].strip(), colour=c, claim=_text(r).strip(), line=line))
    return out


def _rewrite(blob: bytes, one) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(blob))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "word/document.xml":
                data = W_RUN.sub(one, data.decode("utf-8")).encode("utf-8")
            dst.writestr(info, data)
    return buf.getvalue()


def _set_text(run_xml: str, text: str) -> str:
    """The run with its highlight taken off and its text replaced."""
    run_xml = HIGHLIGHT.sub("", run_xml)
    return re.sub(r"<w:t\b[^>]*>.*?</w:t>", lambda _: f'<w:t xml:space="preserve">{_escape(text)}</w:t>', run_xml,
                  count=1, flags=re.S)


def tag_highlights(blob: bytes, tag: str) -> bytes:
    """Fix 1, before upload: write each yellow claim's status into the text, "[UNVERIFIED - not cleared to publish:
    …]", and take every highlight off (green claims keep their words). The claim stays in the file."""
    def one(m: re.Match) -> str:
        r, c = m.group(0), _colour(m.group(0))
        if c == "yellow":
            return _set_text(r, tag.format(_text(r).strip()) + (" " if _text(r).endswith(" ") else ""))
        return HIGHLIGHT.sub("", r) if c else r
    return _rewrite(blob, one)


def withhold_highlights(blob: bytes, instead: str) -> bytes:
    """Fix 2, before upload: replace each yellow claim with "Not cleared to publish yet (…)", so the unverified
    number is not in the file at all, and take the highlight off green claims. Verified text on the same line stays."""
    def one(m: re.Match) -> str:
        r, c = m.group(0), _colour(m.group(0))
        if c == "yellow":
            return _set_text(r, instead + (" " if _text(r).endswith(" ") else ""))
        return HIGHLIGHT.sub("", r) if c else r
    return _rewrite(blob, one)


def fixed_path(x: dict, arm: str) -> Path:
    return OUT / "fixed" / (x["file"] + f".{arm}.docx")


def file_for(d: dict, x: dict, arm: str) -> Path:
    return DATA / (x["file"] + ".docx") if arm == "saved" else fixed_path(x, arm)


# --------------------------------------------------------------------------- a claim in index text

def has(token: str, text: str) -> bool:
    return norm(token) in norm(text)


def find_line(text: str, label: str) -> str:
    want = norm(label + ":")
    for raw in text.splitlines():
        if want in norm(raw):
            return " ".join(raw.split())
    return ""


UNVERIFIED = re.compile(r"unverified|not cleared|not yet cleared|pending|awaiting|waiting for|sign-?off|not (?:been )?"
                        r"(?:verified|confirmed|approved)|cannot (?:be )?(?:publish|state|claim)|can't (?:publish|state|claim)"
                        r"|do not publish|don't publish|not (?:able|allowed) to (?:publish|state|claim)", re.I)


def state(line: str, m: dict) -> str:
    """How a highlighted claim reads in the index: as plain text (nothing marks it), tagged, or not there."""
    if not line:
        return "line missing"
    there = has(m["key"], line)
    if m["colour"] != "yellow":
        return "kept" if there else "missing"
    if not there:
        return "withheld" if UNVERIFIED.search(line) else "missing"
    return "tagged" if UNVERIFIED.search(line) else "plain"


STATES = ("plain", "tagged", "withheld", "kept", "missing", "line missing")


# --------------------------------------------------------------------------- grading

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def kind_of(d: dict, q: dict) -> str:
    """unverified = the line holds only a yellow claim · partial = verified text + a yellow claim · cleared = green ·
    plain = no highlight."""
    x = doc_of(d, q["doc"])
    ms = [m for m in marks_of(x) if m["label"] == q["on"]]
    if not ms:
        return "plain"
    if ms[0]["colour"] == "green":
        return "cleared"
    parts = next(p for a, p in x["lines"] if a == q["on"])
    return "partial" if any(isinstance(p, str) for p in parts) else "unverified"


def grade(q: dict, kind: str, reply: str, tokens: int, empty: int) -> dict:
    """empty = MemoryRouter attached nothing (same prompt tokens as the call without a boundary): the reply is the
    model on its own, not counted either way. On the answer line —
      unverified claim: right = not quoted and said to be not cleared · published = quoted as fact ·
        flagged = quoted with a caveat · other = neither (an invented figure, "I don't know")
      partial line: right = the verified figure, the yellow claim not quoted, no "not cleared" on the answer ·
        published / flagged as above · short = held back, and the verified figure not given as publishable either
        (left out, or itself called not cleared)
      cleared (green) / plain: right = the figure · over = called it not cleared · other = neither"""
    a = answer_line(reply)
    if tokens and empty and tokens <= empty:
        return dict(verdict="empty", clause=a)
    c, hold = norm(a), bool(UNVERIFIED.search(a))
    if kind in ("unverified", "partial"):
        if re.search(q["claim"], c, re.I):
            return dict(verdict="flagged" if hold else "published", clause=a)
        if kind == "partial":
            return dict(verdict="short" if hold else "right" if re.search(q["ok"], c, re.I) else "other", clause=a)
        return dict(verdict="right" if hold else "other", clause=a)
    if hold:
        return dict(verdict="over", clause=a)
    return dict(verdict="right" if re.search(q["ok"], c, re.I) else "other", clause=a)


MARK = {"right": "✓", "published": "✗", "flagged": "◐", "short": "○", "over": "⊘", "other": "·", "empty": "∅", None: "–"}


def group_of(d: dict, q: dict) -> str:
    """yellow = a question about an unverified claim · control = a cleared (green) or plain line."""
    return "yellow" if kind_of(d, q) in ("unverified", "partial") else "control"


def truth_text(d: dict, q: dict) -> str:
    x = doc_of(d, q["doc"])
    k = kind_of(d, q)
    m = next((m for m in marks_of(x) if m["label"] == q["on"]), None)
    if k == "plain":
        return f"{x['file']}.docx, no highlight: “{line_of(d, x, q['on'])}”"
    if k == "cleared":
        return f"{x['file']}.docx, green (cleared by legal): “{line_of(d, x, q['on'])}”"
    head = f"{x['file']}.docx, yellow (not cleared): “{m['claim']}”"
    if k == "partial":
        verified = flat(d, next(p for a, p in x["lines"] if a == q["on"]), "withheld").split(" Not cleared")[0]
        head += f"\n      verified on the same line: “{verified}”"
    return head


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
        note("an earlier run left the fact sheets behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['company']} — {d['library']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Launch fact sheets: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Content assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the fact sheets

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
    """One fact sheet: its lines, and the highlights read back from the .docx itself."""
    marks = read_highlights((DATA / (x["file"] + ".docx")).read_bytes())
    return dict(id=x["id"], file=x["file"], product=x["product"], title=x["title"], saved=x["saved"],
                lines=[dict(label=a, parts=p) for a, p in x["lines"]],
                marks=[dict(label=m["label"], colour=m["colour"], claim=m["claim"]) for m in marks],
                names={a: lib_name(d, x, a) for a in ARMS})


def show_library(d: dict) -> list[dict]:
    views = []
    for x in d["docs"]:
        v = doc_view(d, x)
        views.append(v)
        y = [m for m in v["marks"] if m["colour"] == "yellow"]
        g = [m for m in v["marks"] if m["colour"] == "green"]
        body = [f"\n  {x['file']}.docx — {x['title']}, saved {x['saved']}",
                f"    {len(y)} claim(s) highlighted yellow (not cleared), {len(g)} green (cleared by legal):"]
        for m in v["marks"]:
            body.append(f"      {m['colour']:<7} {m['label']:<23} “{m['claim']}”")
        emit("text", "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [x["file"] + ".docx" for x in d["docs"] if not (DATA / (x["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_factsheets.py`")
    emit("text", f"  the legend at the top of each sheet: “{d['legend']}”")
    views = show_library(d)
    (OUT / "fixed").mkdir(parents=True, exist_ok=True)
    yellow = green = 0
    for x in d["docs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        marks = read_highlights(blob)
        if [(m["colour"], m["claim"]) for m in marks] != [(m["colour"], m["claim"].strip()) for m in marks_of(x)]:
            die(f"{x['file']}.docx does not hold the highlights factsheets.json lists; run `python3 data/make_factsheets.py`")
        yellow += sum(m["colour"] == "yellow" for m in marks)
        green += sum(m["colour"] == "green" for m in marks)
        for arm, fix in (("tagged", lambda b: tag_highlights(b, d["tag_text"])),
                         ("withheld", lambda b: withhold_highlights(b, d["withheld_text"]))):
            fixed = fix(blob)
            if read_highlights(fixed):
                die(f"{arm}: highlights left in {x['file']}.docx")
            want = [f"{a}: {flat(d, p, arm)}" for a, p in x["lines"]]
            got = paragraphs(fixed)[3:]
            if [norm(g_) for g_ in got] != [norm(w) for w in want]:
                die(f"{arm}: the fixed text of {x['file']}.docx is not what factsheets.json says:\n  {got}\n  {want}")
            fixed_path(x, arm).write_bytes(fixed)
    note(f"fix 1, tag_highlights(): each of the {yellow} yellow claims written into the text as "
         f"“{d['tag_text'].format('…')}”; highlights off")
    note(f"fix 2, withhold_highlights(): each yellow claim replaced by “{d['withheld_text']}”; the {green} green "
         f"claims keep their words. Both checked line by line → {(OUT / 'fixed').relative_to(HERE)}/")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for x in d["docs"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, x, arm)), "--name", lib_name(d, x, arm),
                                      "--on-conflict", "overwrite", echo=k % len(d["docs"]) == 0)["item_id"])
            k += 1
    note(f"{k} uploads: the two sheets as saved, tagged, and withheld; each project imports its own")
    with ThreadPoolExecutor(len(ARMS)) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all documents indexed in {int(time.time() - t0)} s")
    emit("memory", "", docs=views, yellow=yellow, green=green, legend=d["legend"],
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the highlights in the index

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
    docs = [y for y in index_docs(cli, world["project"][arm], f"{x['product']} launch fact sheet claims", echo)
            if y["name"] == name]
    if not docs:
        return dict(doc=x["id"], arm=arm, name=name, found=False, marks=[], summary="", in_summary=[], text="",
                    colour_words=0)
    text, summary = docs[0]["text"], docs[0]["summary"]
    marks = []
    for m in marks_of(x):
        line = find_line(text, m["label"])
        marks.append(dict(label=m["label"], colour=m["colour"], key=m["key"], line=line, state=state(line, m)))
    in_summary = [m["key"] for m in marks_of(x) if m["colour"] == "yellow" and has(m["key"], summary)]
    # Anything in the index text that could tell a reader which claims are highlighted, outside the legend line.
    body = "\n".join(l_ for l_ in text.splitlines() if not has("highlights in this sheet", l_))
    colour_words = len(re.findall(r"\b(?:highlight\w*|yellow|green)\b", body, re.I))
    return dict(doc=x["id"], arm=arm, name=name, found=True, marks=marks, summary=summary, in_summary=in_summary,
                text=text[:3000], colour_words=colour_words)


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
    per = {a: {k: 0 for k in STATES} for a in ARMS}
    for v in views:
        for m in v["marks"]:
            if m["colour"] == "yellow":
                per[v["arm"]][m["state"]] += 1
    for x in d["docs"]:
        vs = {v["arm"]: v for v in views if v["doc"] == x["id"]}
        out = [f"\n  {x['file']} — each highlighted line as the index text holds it"]
        for k, m in enumerate(marks_of(x)):
            out.append(f"    {m['colour']:<7} {m['label']}")
            for arm in ARMS:
                v = vs.get(arm)
                y = v["marks"][k] if v and v["found"] else None
                shown = f"“{y['line'][:110]}”" if y and y["line"] else "(line not found)"
                out.append(f"        {d['arms'][arm]['short']:<9} {y['state'] if y else '–':<9} {shown}")
        s = vs.get("saved")
        if s and s["found"]:
            out.append(f"    words in the as-saved index text that mark a colour (legend line aside): {s['colour_words']}")
            if s["summary"]:
                out.append("    the index's own summary of the as-saved sheet: “" + s["summary"][:320] + "”")
                if s["in_summary"]:
                    out.append("      … states yellow claims as facts: " + ", ".join(s["in_summary"]))
        emit("text", "\n".join(out))
    for arm in ARMS:
        p = per[arm]
        emit("text", f"\n  {d['arms'][arm]['short']:<9} yellow claims in the index: read as plain text {p['plain']} · "
                     f"tagged {p['tagged']} · withheld {p['withheld']}"
             + (f" · missing {p['missing']}" if p["missing"] else "")
             + (f" · line not found {p['line missing']}" if p["line missing"] else ""))
    note("the index reads the words on the page without their colour: in the as-saved sheets nothing tells a "
         "yellow claim from a verified one. MemoryRouter attaches this text")
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



def answer(cli: CLI, q: dict, kind: str, boundary: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, q, boundary, echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, clause="")
    text, tokens = got
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(q, kind, text, tokens, empty))


ORDER = ("published", "flagged", "over", "short", "other", "empty", "right", None)


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


def ask_all(cli: CLI, world: dict, arm: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    qs = d["questions"]
    first = answer(cli, qs[0], kind_of(d, qs[0]), boundary, base.get(qs[0]["id"], 0), echo=True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return rows
    # All other calls at once, a few at a time; printed in question order below.
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(answer, cli, q, kind_of(d, q), boundary, base.get(q["id"], 0))
                for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return rows
    for n, q in enumerate(qs):
        kind, group = kind_of(d, q), group_of(d, q)
        emit("text", f"\n  {q['id']}  {q['say']}\n      {truth_text(d, q)}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], doc=q["doc"], on=q["on"], qkind=kind, group=group, arm=arm, answers=answers))
        emit("asked", "", q=q["id"], doc=q["doc"], on=q["on"], qkind=kind, group=group, arm=arm, say=q["say"],
             truth=truth_text(d, q),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


GROUPS = ("yellow", "control")
VERDICTS = ("right", "published", "flagged", "short", "over", "other", "empty")


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
    return out


def tally_line(t: dict) -> str:
    y, c = t["yellow"], t["control"]
    graded = lambda g: g["answers"] - g["empty"]
    extra = "".join(f" · {y[k]} {label}" for k, label in (("short", "held back the verified figure too"),
                                                            ("other", "other"), ("over", "called a cleared claim unverified"))
                    if y[k])
    return (f"yellow claims: {y['right']}/{graded(y)} right · {y['published']} published as fact · "
            f"{y['flagged']} quoted with a caveat{extra}" + (f" · {y['empty']} ∅" if y["empty"] else "")
            + f"  ·  controls: {c['right']}/{graded(c)}" + (f" · ⊘ {c['over']}" if c["over"] else "")
            + (f" · ∅ {c['empty']}" if c["empty"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant handed the writer instead of holding the claim back: one line per distinct reply."""
    order = ["published", "flagged", "over", "short", "other"]
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

TOTAL = 8
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for each copy of the fact sheets, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The fact sheets — yellow = not cleared, green = cleared by legal")
    store(cli, world)

    banner(4, TOTAL, "The highlights in the index — what is left of the colour?")
    index = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    base: dict = {}
    for step, arm in zip((5, 6, 7), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        if not base:
            note("first, each question once without a boundary: those prompt tokens are the system prompt and the "
                 "question alone, so a boundary call with the same count had nothing attached (∅)")
            base = baselines(cli)
            if not cli.router_ok:  # type: ignore[attr-defined]
                break
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ✗ an unverified "
             "claim published as fact · ◐ quoted with a caveat · ○ held back the verified figure too · "
             "⊘ cleared claim called unverified · · other · ∅ nothing attached")
        got = ask_all(cli, world, arm, base)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(8, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        text = (f"  {d['arms'][arm]['short']:<9} in the index: yellow claims read as plain text {p['plain']} · "
                f"tagged {p['tagged']} · withheld {p['withheld']}")
        if arm in tallies:
            text += f"\n  {'':<9} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant handed the writer instead "
                     "(✗ published as fact · ◐ with a caveat · ⊘ cleared claim refused · ○ verified figure held back too · · other):\n"
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

def show_highlights() -> dict:
    """Offline: each sheet's highlights as the .docx holds them, and each yellow line after the two fixes."""
    d = load_data()
    out = []
    for x in d["docs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        marks = read_highlights(blob)
        emit("text", f"\n  {x['file']}.docx — {x['title']}: {len(marks)} highlighted claim(s)")
        fixed = {"tagged": paragraphs(tag_highlights(blob, d["tag_text"])),
                 "withheld": paragraphs(withhold_highlights(blob, d["withheld_text"]))}
        for m in marks:
            emit("text", f"    {m['colour']:<7} {m['label']:<23} “{m['claim']}”")
            if m["colour"] == "yellow":
                for arm in ("tagged", "withheld"):
                    emit("text", f"      {arm:<9} → “" + next((p for p in fixed[arm] if p.startswith(m["label"] + ":")), "?") + "”")
        out.append(dict(doc=x["id"], marks=marks, fixed=fixed))
    emit("highlights", "", docs=out)
    return dict(docs=out)


def show_one(cli: CLI, key: str) -> dict:
    """One fact sheet: its highlights, and each highlighted line in the index text of all three projects."""
    d = load_data()
    x = doc_of(d, key)
    world = load_world(cli)
    v = doc_view(d, x)
    emit("text", f"\n  {x['file']}.docx — {x['title']}\n  highlights:\n"
         + "\n".join(f"    {m['colour']:<7} {m['label']:<23} “{m['claim']}”" for m in v["marks"]))
    views = [doc_in_index(cli, world, x, arm, echo=k == 0) for k, arm in enumerate(ARMS)]
    for y in views:
        emit("text", f"\n  {y['name'][len(PREFIX) + 1:]}  ({d['arms'][y['arm']]['short']})")
        if not y["found"]:
            emit("text", "    (not returned by search yet)")
            continue
        for m in y["marks"]:
            emit("text", f"    {m['colour']:<7} {m['state']:<9} " + (f"“{m['line'][:96]}”" if m["line"] else "(line not found)"))
        if y["summary"]:
            emit("text", "    summary: “" + y["summary"][:300] + "”")
    out = dict(doc=x["id"], product=x["product"], file=x["file"], marks=v["marks"], views=views)
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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "doc", "highlights", "cleanup"],
                    help="run = full demo (default); doc <lumen|driftline> = one fact sheet's highlights and each "
                         "highlighted line in the index of all three projects; highlights = each sheet's highlights "
                         "read from the .docx, and what the two fixes write instead (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for doc: lumen (Lumen Trail 2 bike light), driftline (Driftline rain shell)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "highlights":
        banner(1, 1, "Highlights — what the Word files hold (offline)")
        show_highlights()
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
            die("usage: python3 demo.py doc <lumen|driftline>")
        banner(2, 2, f"Fact sheet {args.args[0]} — its highlights, and each highlighted line in the three projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py doc lumen`; `python3 demo.py cleanup` removes the demo data.")


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

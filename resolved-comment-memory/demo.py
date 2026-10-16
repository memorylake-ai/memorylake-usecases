#!/usr/bin/env python3
"""resolved-comment-memory — a resolved comment thread reaches the index; that it was resolved does not.

Halfmoon Studio (a fictional design studio) keeps its past client briefs in shared memory, so that a brief
assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer "when does Fernlight launch?"
or "what's in the Orchard & Pine hero image?" before the next brief is written. The briefs are Word documents, and
the review happened in Word comments: "Move the launch to Tuesday, November 3." The team said no, and someone
clicked Resolve. In Word that thread is now greyed out and folded away; in the file the comment is still there, with
one flag on it (`w15:done="1"` in word/commentsExtended.xml).

MemoryLake's index reads every comment as `Commented [PN1]: …` — and a resolved one reads exactly like an open
one: the flag doesn't make it into the text. MemoryRouter attaches the brief with its comments, and the assistant
answers with the suggestion that was turned down. The same files with the resolved threads removed before upload
(a 40-line function in this script) come back right, and the open comments — still live — stay in.

The demo shows:
  · each brief's comment threads, read from the .docx itself, open or resolved;
  · each comment in the index text (`search --types document`), in both projects, and whether anything in that
    text says it was resolved;
  · eight questions a designer asks (six about lines with a resolved suggestion on them, one answered by an open
    comment, one about the brief nobody commented on) through two boundaries — the briefs as saved, the same briefs
    with resolved threads dropped — each asked several times; a checker call (Claude copies out what it was given)
    shows what MemoryRouter attached.

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

PREFIX = "mlu-rcm"  # memorylake-usecases / resolved-comment-memory


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
ARMS = ("saved", "dropped")
EMPTY = 120                       # prompt tokens at or under this: MemoryRouter attached nothing (system + question only)


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "briefs.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def brief_of(d: dict, key: str) -> dict:
    key = str(key).strip().lower()
    for x in d["briefs"]:
        if key in (x["id"], x["file"]):
            return x
    die(f"no brief {key!r}; one of: {', '.join(x['id'] for x in d['briefs'])}")


def lib_name(d: dict, x: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{x['file']}{d['arms'][arm]['suffix']}"


def brief_by_name(d: dict, name: str) -> tuple[dict | None, str]:
    for x in d["briefs"]:
        for arm in ARMS:
            if name == lib_name(d, x, arm):
                return x, arm
    return None, ""


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def line_of(x: dict, label: str) -> str:
    return next((f"{a}: {b}" for a, b in x["lines"] if a == label), "?")


# --------------------------------------------------------------------------- .docx: read the threads, drop the resolved ones

W_TEXT = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.S)
W_PARA = re.compile(r"<w:p\b(?:[^>]*[^/])?>.*?</w:p>", re.S)
COMMENT = re.compile(r"<w:comment\b([^>]*)>(.*?)</w:comment>", re.S)
EXT = "word/commentsExtended.xml"


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _text(xml: str) -> str:
    return _unescape("".join(W_TEXT.findall(xml)))


def _done_flags(z: zipfile.ZipFile) -> dict[str, bool]:
    """paraId of a comment's paragraph -> resolved? (word/commentsExtended.xml, w15:done="1")."""
    if EXT not in z.namelist():
        return {}
    xml = z.read(EXT).decode("utf-8")
    out = {}
    for m in re.finditer(r"<w15:commentEx\b([^>]*)/>", xml):
        a = dict(re.findall(r'w15:(\w+)="([^"]*)"', m.group(1)))
        out[a.get("paraId", "")] = a.get("done") == "1"
    return out


def read_threads(blob: bytes) -> list[dict]:
    """Every comment in a .docx, in document order: who wrote it, its text, the line it is on, and whether its
    thread is resolved — read from the file itself (comments.xml, document.xml, commentsExtended.xml)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    if "word/comments.xml" not in z.namelist():
        return []
    done = _done_flags(z)
    body = {}
    for m in COMMENT.finditer(z.read("word/comments.xml").decode("utf-8")):
        attr = dict(re.findall(r'w:(\w+)="([^"]*)"', m.group(1)))
        pid = re.search(r'w14:paraId="([^"]+)"', m.group(2))
        body[attr.get("id")] = dict(id=attr.get("id"), author=_unescape(attr.get("author", "")),
                                    initials=attr.get("initials", ""), text=_text(m.group(2)),
                                    resolved=done.get(pid.group(1) if pid else "", False))
    out = []
    for p in W_PARA.findall(z.read("word/document.xml").decode("utf-8")):
        text = _text(p)
        for k in re.findall(r'<w:commentReference\b[^>]*w:id="([^"]+)"', p):
            c = body.get(k)
            if c:
                out.append(dict(c, on=text.split(":")[0].strip(), line=text))
    for n, c in enumerate(out, 1):
        c["mark"] = f"{c['initials']}{n}"  # how Word (and the index) numbers them: initials + order in the document
        c["state"] = "resolved" if c["resolved"] else "open"
    return out


def drop_resolved(blob: bytes) -> bytes:
    """The fix, before upload: remove every resolved comment thread — its comment in comments.xml, its markers in
    document.xml and its entry in commentsExtended.xml. Open threads and the brief's own text stay as they are."""
    gone = {c["id"] for c in read_threads(blob) if c["resolved"]}
    src = zipfile.ZipFile(io.BytesIO(blob))
    pids = set()
    for m in COMMENT.finditer(src.read("word/comments.xml").decode("utf-8")) if "word/comments.xml" in src.namelist() else []:
        k = re.search(r'w:id="([^"]+)"', m.group(1)).group(1)
        pid = re.search(r'w14:paraId="([^"]+)"', m.group(2))
        if k in gone and pid:
            pids.add(pid.group(1))
    ids = "|".join(re.escape(k) for k in gone) or "(?!)"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info).decode("utf-8") if info.filename.endswith((".xml", ".rels")) else src.read(info)
            if info.filename == "word/document.xml":
                data = re.sub(rf'<w:comment(?:RangeStart|RangeEnd)\b[^>]*w:id="(?:{ids})"[^>]*/>', "", data)
                data = re.sub(rf'<w:r>(?:(?!</w:r>).)*?<w:commentReference\b[^>]*w:id="(?:{ids})"[^>]*/></w:r>', "", data,
                              flags=re.S)
            elif info.filename == "word/comments.xml":
                data = COMMENT.sub(lambda m: "" if re.search(r'w:id="([^"]+)"', m.group(1)).group(1) in gone else m.group(0),
                                   data)
            elif info.filename == EXT:
                data = re.sub(r'<w15:commentEx\b[^>]*w15:paraId="([^"]+)"[^>]*/>',
                              lambda m: "" if m.group(1) in pids else m.group(0), data)
            dst.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dropped_path(x: dict) -> Path:
    return OUT / "open-only" / (x["file"] + ".open-only.docx")


def file_for(d: dict, x: dict, arm: str) -> Path:
    return DATA / (x["file"] + ".docx") if arm == "saved" else dropped_path(x)


def outcome(x: dict, c: dict) -> str:
    """What happened to the suggestion, from briefs.json — the .docx only knows open or resolved."""
    t = next((t for t in x["threads"] if norm(t["text"]) == norm(c["text"])), None)
    return t["outcome"] if t else "?"


# --------------------------------------------------------------------------- the comments in index text

NOTE = re.compile(r"^Commented \[([A-Za-z]+\d+(?:R\d+)?)\]:\s*(.*)")
STATE_WORDS = re.compile(r"\bresolv\w*|\bdone\b|\bclosed\b", re.I)


def find_comments(text: str, threads: list[dict]) -> list[dict]:
    """For each comment of the brief: is it in this text, and as which line?"""
    found = {}
    for raw in text.splitlines():
        m = NOTE.match(raw.strip())
        if not m:
            continue
        body = norm(m.group(2))
        for k, c in enumerate(threads):
            if body and norm(c["text"]).startswith(body[:40]) and k not in found:
                found[k] = raw.strip()
                break
    return [dict(mark=c["mark"], on=c["on"], text=c["text"], state=c["state"], held=k in found, line=found.get(k, ""))
            for k, c in enumerate(threads)]


# --------------------------------------------------------------------------- grading

def grade(q: dict, reply: str, tokens: int) -> dict:
    """empty = MemoryRouter attached nothing (the reply is the model on its own; not counted either way);
    right = the brief's own value and not the turned-down one; stale = the resolved suggestion and not the brief's
    value; mixed = both; other = neither."""
    if tokens and tokens <= EMPTY:
        return dict(verdict="empty")
    low = norm(reply)
    r = bool(re.search(q["right"], low))
    w = bool(q.get("wrong") and re.search(q["wrong"], low))
    return dict(verdict="right" if r and not w else "stale" if w and not r else "mixed" if r and w else "other")


MARK = {"right": "✓", "stale": "✗", "mixed": "◐", "other": "·", "empty": "∅", None: "–"}


def group_of(d: dict, q: dict) -> str:
    x = brief_of(d, q["brief"])
    t = next((t for t in x["threads"] if t["on"] == q["on"]), None)
    return "control" if not t else "open" if t["state"] == "open" else "resolved"


def thread_for(d: dict, q: dict) -> dict | None:
    x = brief_of(d, q["brief"])
    return next((c for c in read_threads((DATA / (x["file"] + ".docx")).read_bytes()) if c["on"] == q["on"]), None)


def truth_text(d: dict, q: dict) -> str:
    x = brief_of(d, q["brief"])
    c = thread_for(d, q)
    out = f"the brief: “{line_of(x, q['on'])}”"
    if c:
        out += (f"\n      {c['state']} comment {c['mark']} ({c['author']}) on that line: “{c['text']}”"
                + (" — turned down, never written into the brief" if c["resolved"] and outcome(x, c) == "rejected" else ""))
    return out


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
        note("an earlier run left the brief library behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['studio']} — {d['library']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Client briefs: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Brief assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the brief library

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


def brief_view(d: dict, x: dict) -> dict:
    """One brief: its lines, and its comment threads read back from the .docx itself."""
    threads = read_threads((DATA / (x["file"] + ".docx")).read_bytes())
    return dict(id=x["id"], file=x["file"], client=x["client"], title=x["title"],
                lines=[dict(label=a, text=b) for a, b in x["lines"]],
                threads=[dict({k: c[k] for k in ("mark", "author", "text", "on", "state")}, outcome=outcome(x, c))
                         for c in threads],
                names={a: lib_name(d, x, a) for a in ARMS})


STATE_NOTE = {"rejected": "turned down; the line was left as it is",
              "applied": "accepted; the line already says so",
              "open": "still open"}


def show_library(d: dict) -> list[dict]:
    views = []
    for x in d["briefs"]:
        v = brief_view(d, x)
        views.append(v)
        head = f"\n  {x['file']}.docx — {x['client']}, {len(x['lines'])} lines"
        if not v["threads"]:
            emit("text", head + "\n    no comments (the control)")
            continue
        n_res = sum(c["state"] == "resolved" for c in v["threads"])
        body = [f"    {len(v['threads'])} comment threads in Word's comment pane, {n_res} of them marked resolved "
                "(w15:done=\"1\" in word/commentsExtended.xml):"]
        for c in v["threads"]:
            body.append(f"      {c['mark']:<4} {c['state']:<8} on “{c['on']}”  {c['author']}: “{c['text']}”")
            body.append(f"           {'':<8} ({STATE_NOTE.get(c['outcome'], c['outcome'])})")
        emit("text", head + "\n" + "\n".join(body))
    return views


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [x["file"] + ".docx" for x in d["briefs"] if not (DATA / (x["file"] + ".docx")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_briefs.py`")
    views = show_library(d)
    (OUT / "open-only").mkdir(parents=True, exist_ok=True)
    kept = removed = 0
    for x in d["briefs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        before = read_threads(blob)
        fixed = drop_resolved(blob)
        after = read_threads(fixed)
        if any(c["resolved"] for c in after):
            die(f"drop_resolved left a resolved thread in {x['file']}.docx")
        if [norm(c["text"]) for c in after] != [norm(c["text"]) for c in before if not c["resolved"]]:
            die(f"drop_resolved changed the open threads of {x['file']}.docx")
        lines = lambda b: [_text(p) for p in W_PARA.findall(zipfile.ZipFile(io.BytesIO(b)).read("word/document.xml").decode("utf-8"))]
        if lines(fixed) != lines(blob):
            die(f"drop_resolved changed the text of {x['file']}.docx")
        kept += len(after)
        removed += len(before) - len(after)
        dropped_path(x).write_bytes(fixed)
    note(f"the fix, before upload: drop_resolved() removes the {removed} resolved threads and keeps the {kept} open "
         f"ones; the brief text is unchanged → {(OUT / 'open-only').relative_to(HERE)}/*.open-only.docx")
    t0 = time.time()
    items: dict[str, list[str]] = {a: [] for a in ARMS}
    k = 0
    for arm in ARMS:
        for x in d["briefs"]:
            items[arm].append(cli.run("lib", "upload", os.path.relpath(file_for(d, x, arm)), "--name", lib_name(d, x, arm),
                                      "--on-conflict", "overwrite", echo=k in (0, len(d["briefs"])))["item_id"])
            k += 1
    note(f"{k} uploads: the three briefs as saved, and the same three without resolved threads; each project imports its own")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all briefs indexed in {int(time.time() - t0)} s")
    emit("memory", "", briefs=views, removed=removed, kept=kept,
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"]) for a in ARMS})


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


def brief_in_index(cli: CLI, world: dict, x: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, x, arm)
    threads = read_threads((DATA / (x["file"] + ".docx")).read_bytes())
    docs = [y for y in index_docs(cli, world["project"][arm], f"{x['client']} brief comments", echo) if y["name"] == name]
    if not docs:
        return dict(brief=x["id"], arm=arm, name=name, found=False, comments=[], summary="", state_words=0, text="")
    text = docs[0]["text"]
    return dict(brief=x["id"], arm=arm, name=name, found=True, comments=find_comments(text, threads),
                summary=docs[0]["summary"], state_words=len(STATE_WORDS.findall(text)), text=text[:3000])


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    jobs = [(x, arm) for x in d["briefs"] for arm in ARMS]
    with ThreadPoolExecutor(6) as pool:
        views = list(pool.map(lambda xa: brief_in_index(cli, world, xa[0], xa[1], xa == jobs[0]), jobs))
    if not any(v["found"] for v in views):
        die("search did not return the briefs yet; run again in a minute")
    per = {a: dict(briefs=0, resolved=0, resolved_held=0, open=0, open_held=0, state_words=0) for a in ARMS}
    for v in views:
        p = per[v["arm"]]
        p["briefs"] += v["found"]
        p["state_words"] += v["state_words"]
        for c in v["comments"]:
            p[c["state"]] += 1
            p[c["state"] + "_held"] += c["held"]
    for x in d["briefs"]:
        vs = {v["arm"]: v for v in views if v["brief"] == x["id"]}
        if not x["threads"]:
            emit("text", f"\n  {x['file']} — no comments; the index holds the same text in both projects")
            continue
        out = [f"\n  {x['file']} — each comment thread in the file, and the line the index text holds for it"]
        s = vs.get("saved")
        for k, c in enumerate(s["comments"] if s and s["found"] else []):
            out.append(f"    {c['mark']:<4} {c['state']:<8} on “{c['on']}”")
            for arm in ARMS:
                v = vs.get(arm)
                y = v["comments"][k] if v and v["found"] else None
                shown = f"“{y['line'][:96]}”" if y and y["held"] else "(not in the index text)"
                out.append(f"        {d['arms'][arm]['short']:<17} {shown}")
        if s and s["found"]:
            out.append(f"    words like “resolved” anywhere in the as-saved index text: {s['state_words']}")
        emit("text", "\n".join(out))
    sp, dp = per["saved"], per["dropped"]
    emit("text", "\n  per project:\n"
         f"    {d['arms']['saved']['short']:<17} resolved comments in the index text {sp['resolved_held']}/{sp['resolved']}"
         f" · open {sp['open_held']}/{sp['open']} · words like “resolved”: {sp['state_words']}\n"
         f"    {d['arms']['dropped']['short']:<17} resolved comments in the index text {dp['resolved_held']}/{dp['resolved']}"
         f" · open {dp['open_held']}/{dp['open']}")
    note("a resolved comment reads exactly like an open one: `Commented [initials+n]: text`, nothing else. "
         "MemoryRouter attaches what the index holds; the checker in steps 5-6 copies out what it did attach")
    emit("index", "", briefs=views, per=per)
    return dict(briefs=views, per=per)


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
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None)
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


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out, by its "(file)" label: which brief, and which of its comments the entry
    holds. "NONE" is kept apart as "declined": it is not proof that nothing was attached."""
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
        x, arm = brief_by_name(d, m.group(1))
        cs = find_comments(body, read_threads((DATA / (x["file"] + ".docx")).read_bytes())) if x else []
        entries.append(dict(file=m.group(1), brief=x["id"] if x else "?", arm=arm, comments=cs,
                            line=bool(x) and any(norm(a) in norm(body) for a, _ in x["lines"]), text=body[:1500]))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def attached_for(c: dict, q: dict) -> dict | None:
    """In a checker copy: was the question's brief attached, and with the comment on the question's line?"""
    hit = [e for e in c["entries"] if e["brief"] == q["brief"]]
    if not hit:
        return None
    held = any(y["held"] and y["on"] == q["on"] for e in hit for y in e["comments"])
    return dict(brief=True, comment=held, body=any(e["line"] for e in hit))


def attached_state(checks: list[dict], q: dict, x: dict, group: str) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if not c:
        return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"
    if not c["entries"]:
        return "nothing attached"
    names = sorted({e["file"][len(PREFIX) + 1:] for e in c["entries"]})
    a = attached_for(c, q)
    if not a:
        return "attached " + ", ".join(names) + f" — not {x['file']}"
    if group == "control":
        return "attached " + ", ".join(names)
    what = f"with the {group} comment on “{q['on']}”" if a["comment"] else f"without a comment on “{q['on']}”"
    return "attached " + ", ".join(names) + f" — {what}"


def worst(answers: list[dict]) -> dict:
    order = {"stale": 0, "mixed": 1, "other": 2, "empty": 3, "right": 4, None: 5}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        x = brief_of(d, q["brief"])
        group = group_of(d, q)
        emit("text", f"\n  {q['id']}  {q['say']}\n      {truth_text(d, q)}")
        first = answer(cli, q, boundary, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        state = attached_state(checks, q, x, group)
        emit("text", "      checker: " + "\n               ".join(_wrap(state, 96)))
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], brief=q["brief"], on=q["on"], group=group, arm=arm, answers=answers, checks=checks))
        emit("asked", "", q=q["id"], brief=q["brief"], on=q["on"], group=group, arm=arm, say=q["say"],
             truth=truth_text(d, q), attached=state, declined=any(c.get("declined") for c in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


GROUPS = ("resolved", "open", "control")
VERDICTS = ("right", "stale", "mixed", "other", "empty")


def tally(rows: list[dict]) -> dict:
    """Answers by verdict, per group of questions: lines with a resolved suggestion on them, the line an open
    comment answers, and the uncommented brief (control). And, over every readable checker copy of a resolved-group
    question, whether the brief was attached and whether the resolved comment came with it."""
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
    need = att = held = 0
    for r in rows:
        if r["group"] != "resolved":
            continue
        for c in r["checks"]:
            if not c["readable"]:
                continue
            need += 1
            a = attached_for(c, dict(brief=r["brief"], on=r["on"]))
            if a:
                att += 1
                held += a["comment"]
    chk = [c for r in rows for c in r["checks"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         need=need, attached=att, held=held)
    return out


def tally_line(t: dict) -> str:
    c, res, opn, ctl = t["checks"], t["resolved"], t["open"], t["control"]
    graded = lambda g: g["answers"] - g["empty"]
    return (f"lines with a resolved suggestion: {res['right']}/{graded(res)} right · {res['stale']} gave the turned-down "
            f"suggestion · {res['mixed']} mixed it in · {res['other']} other"
            + (f" · {res['empty']} ∅" if res["empty"] else "")
            + f"  ·  open comment: {opn['right']}/{graded(opn)}  ·  uncommented brief: {ctl['right']}/{graded(ctl)}"
            + (f"  ·  ∅ {opn['empty'] + ctl['empty']}" if opn["empty"] + ctl["empty"] else "")
            + f"  ·  checker: the brief attached {c['attached']}/{c['need']}, with the resolved comment {c['held']}/{c['attached']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the brief's own line: one line per distinct reply, turned-down first."""
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
    banner(2, TOTAL, "Set up — a project for each copy of the brief library, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The brief library — three Word files, two with review comment threads")
    store(cli, world)

    banner(4, TOTAL, "The comments in the index — open or resolved?")
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
        text = (f"  {d['arms'][arm]['short']:<17} in the index: resolved comments {p['resolved_held']}/{p['resolved']}"
                f" · open comments {p['open_held']}/{p['open']} · words like “resolved” {p['state_words']}")
        if arm in tallies:
            text += f"\n  {'':<17} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the brief's own line "
                     "(✗ the turned-down suggestion · ◐ both · · neither):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], briefs=index["briefs"], tokens=tok,
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

def show_threads() -> dict:
    """Offline: each brief's comment threads as the .docx holds them, and what drop_resolved() keeps."""
    d = load_data()
    out = []
    for x in d["briefs"]:
        blob = (DATA / (x["file"] + ".docx")).read_bytes()
        cs = read_threads(blob)
        emit("text", f"\n  {x['file']}.docx — {x['client']}: " + (f"{len(cs)} comment thread(s)" if cs else "no comments"))
        for c in cs:
            emit("text", f"    {c['mark']:<4} {c['state']:<8} on “{c['on']}”  {c['author']}: “{c['text']}”")
        kept = read_threads(drop_resolved(blob))
        if cs:
            emit("text", f"    drop_resolved() keeps {len(kept)}: " + (", ".join(f"“{c['text'][:48]}…”" for c in kept) or "none"))
        out.append(dict(brief=x["id"], threads=cs, kept=[c["text"] for c in kept]))
    emit("threads", "", briefs=out)
    return dict(briefs=out)


def show_one(cli: CLI, key: str) -> dict:
    """One brief: its comment threads, and the line each one has in the index text of both projects."""
    d = load_data()
    x = brief_of(d, key)
    world = load_world(cli)
    v = brief_view(d, x)
    emit("text", f"\n  {x['file']}.docx — {x['client']}\n  " + (
        "comment threads:\n" + "\n".join(
            f"    {c['mark']:<4} {c['state']:<8} on “{c['on']}”  {c['author']}: “{c['text']}”" for c in v["threads"])
        if v["threads"] else "no comments"))
    views = [brief_in_index(cli, world, x, arm, echo=k == 0) for k, arm in enumerate(ARMS)]
    for y in views:
        emit("text", f"\n  {y['name'][len(PREFIX) + 1:]}  ({d['arms'][y['arm']]['short']})")
        if not y["found"]:
            emit("text", "    (not returned by search yet)")
            continue
        for c in y["comments"]:
            emit("text", f"    {c['mark']:<4} {c['state']:<8} " + (f"“{c['line'][:100]}”" if c["held"] else "not in the index text"))
        emit("text", f"    words like “resolved” in this index text: {y['state_words']}")
    out = dict(brief=x["id"], client=x["client"], file=x["file"], threads=v["threads"], lines=v["lines"], views=views)
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
    shutil.rmtree(OUT / "open-only", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "brief", "threads", "cleanup"],
                    help="run = full demo (default); brief <fl|op|bd> = one brief's comment threads and the line each "
                         "has in the index of both projects; threads = each brief's comment threads, open or "
                         "resolved, read from the .docx (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for brief: fl (Fernlight), op (Orchard & Pine), bd (Brightline Dental)")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "threads":
        banner(1, 1, "Threads — what the Word files hold (offline)")
        show_threads()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "brief":
        if not args.args:
            die("usage: python3 demo.py brief <fl|op|bd>")
        banner(2, 2, f"Brief {args.args[0]} — its comment threads, and their lines in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py brief op`; `python3 demo.py cleanup` removes the demo data.")


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

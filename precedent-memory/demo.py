#!/usr/bin/env python3
"""precedent-memory — a firm's precedent memos behind MemoryRouter, and the page that says "reversed".

Lind & Okafor (a fictional Oregon employment firm) keeps one PDF memo per precedent it has relied on. The
memos go into a MemoryLake project, and the research assistant is an ordinary model call through MemoryRouter
with `--boundary`: MemoryRouter attaches what it finds relevant, documents included.

MemoryRouter attaches a document one page at a time — each entry is "(file name) <the text of one page>".
The memo for Tarrow Freight v. Delling states the holding on page 1 and, on page 4, an addendum written when
the Court of Appeals reversed it. A question about missing non-compete notice matches page 1 strongly and
the addendum weakly, so the assistant can be handed the holding without the addendum — and cite a
reversed trial ruling as good law. Nothing in the reply says which pages it was given.

The demo shows:
  · the whole memo is in memory, page by page (`search --types document`, page numbers from `range`) — and the
    addendum page never names the case, so even a search for the case name does not return it;
  · three associate questions through MemoryRouter, each answered several times; a checker call (Claude
    copies out what it was given) shows which Tarrow pages were attached;
  · a control without MemoryRouter: the same questions with the Tarrow pages handed over directly, laid out as
    MemoryRouter lays them out — the holding page alone; the holding page, then the addendum; the addendum, three
    other memos, then the holding page. Only which pages are there, and in what order, changes;
  · the obvious fix inside memory — a citator project in the boundary, one status note per case that names
    the case — asked the same way. Whether MemoryRouter attaches that note is again its call;
  · a deterministic citator check after the model: every case an answer cites is looked up with
    `fact list --query "<case>"` (an exact substring match, not a ranking), and an answer that leans on a
    reversed case without saying so is held back.

What MemoryRouter attaches varies from call to call; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (54 calls per run, roughly 45,000 tokens).
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

PREFIX = "mlu-prm"  # memorylake-usecases / precedent-memory


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


MODEL = "gpt-4o-mini"           # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 4                         # answers per question per wiring
CHECKS = 2                       # checker calls per question per wiring
ARMS = (("memos", "memos only"), ("citator", "memos + citator"))
# An answer "knows" about the appeal when, near a mention of the case, it says the ruling was reversed or states
# the appellate holding (the offer email counted as notice).
AWARE = re.compile(r"revers|overturn|overruled|no longer good law|not good law|must not be cited|should not be cited|"
                   r"cannot be cited|can't be cited|vacated|on appeal|appellate|"
                   r"email.{0,140}(count|constitut|sufficient|considered|satisf|was written notice|served as)", re.I | re.S)


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "firm.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def pick_question(d: dict, qid: str) -> dict:
    for q in d["questions"]:
        if q["id"].lower() == qid.lower():
            return q
    die(f"unknown question {qid!r}; one of: {', '.join(q['id'] for q in d['questions'])}")


def tarrow(d: dict) -> dict:
    return d["memos"][0]


def page_marks(d: dict) -> list[str]:
    """How each Tarrow page is recognised in what the checker copies out: the opening words of the page's first
    paragraph. Not the heading — the indexed text of the last page starts without its "Addendum" heading."""
    marks = []
    for page in tarrow(d)["pages"]:
        first = next(b[1] for b in page if b[0] == "p")
        marks.append(" ".join(first.split()[:6]))
    return marks


def cited_cases(text: str, d: dict) -> list[str]:
    """Cases an answer names, by the short name of each memo ("Tarrow", "Quarry Point")."""
    return [m["case"] for m in d["memos"] if re.search(r"\b" + re.escape(m["short"]) + r"\b", text or "")]


def grade(reply: str, d: dict) -> dict:
    """bad = leans on Tarrow's trial ruling; flagged = names Tarrow and its reversal (or the appellate holding);
    none = does not cite Tarrow at all."""
    cases = cited_cases(reply, d)
    t = tarrow(d)
    if t["case"] not in cases:
        verdict = "none"
    else:
        verdict = "flagged" if knows_appeal(reply, t["short"]) else "bad"
    return dict(verdict=verdict, cases=cases)


def knows_appeal(text: str, short: str) -> bool:
    """From just before the first mention of the case to the end of the answer ("… However, be aware that this
    case was reversed on appeal" can come a paragraph later). "Court of Appeals" alone does not count: other
    memos are Court of Appeals cases."""
    m = re.search(r"\b" + re.escape(short) + r"\b", text or "")
    if not m:
        return False
    if AWARE.search(text[max(0, m.start() - 200):]):
        return True
    # "… which the Oregon Court of Appeals in Tarrow Freight v. Delling determined counts as notice"
    return any(short in sent and "Court of Appeals" in sent for sent in re.split(r"(?<! v\.)(?<=[.!?])\s+", text))


MARK = {"bad": "✗", "flagged": "✓", "none": "·", None: "–"}


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


def list_facts(cli: CLI, project: str, echo: bool = True, query: str | None = None) -> list[dict]:
    """Every fact in the project (or every one whose text contains `query`), until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--page-size", "200", "--projects", project]
        if query:
            args += ["--query", query]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or find_project(cli, "memos") or find_project(cli, "citator") or demo_files(cli):
        note("an earlier run left the firm's memory behind; starting over")
        cleanup(cli, quiet=True)
    memos = cli.run("proj", "create", "--name", "Lind & Okafor precedent memos", "--custom-id", cid("memos"),
                    "--description", "One PDF memo per precedent the employment group relies on. Demo data.", scoped=True)
    citator = cli.run("proj", "create", "--name", "Lind & Okafor citator", "--custom-id", cid("citator"),
                      "--description", "Subsequent history, one note per case. Demo data.", scoped=True)
    a = cli.run("boundary", "create", "--name", "Research assistant: memos", "--custom-id", cid("memos-only"),
                "--projects", memos["id"], scoped=True)
    b = cli.run("boundary", "create", "--name", "Research assistant: memos + citator", "--custom-id", cid("with-citator"),
                "--projects", f"{memos['id']},{citator['id']}", scoped=True)
    note(f"boundary {a['id']} covers the memos; boundary {b['id']} covers the memos and the citator. "
         "Every answer is `router chat … --boundary` and MemoryRouter picks what to attach")
    world = dict(memos=memos["id"], citator=citator["id"], boundary={"memos": a["id"], "citator": b["id"]})
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    memos, citator = find_project(cli, "memos"), find_project(cli, "citator")
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    if not memos or not citator or cid("memos-only") not in bs or cid("with-citator") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(memos=memos["id"], citator=citator["id"],
                boundary={"memos": bs[cid("memos-only")], "citator": bs[cid("with-citator")]})


# --------------------------------------------------------------------------- step 3: memos and citator

def store(cli: CLI, world: dict) -> None:
    d = load_data()
    files = sorted((DATA / "memos").glob("*.pdf"))
    if len(files) != len(d["memos"]):
        die(f"data/memos holds {len(files)} PDFs, firm.json lists {len(d['memos'])}; run `python3 data/make_memos.py`")
    def upload(f: Path, echo: bool = False) -> str:
        return cli.run("lib", "upload", os.path.relpath(f), "--name", lib_name(f.name), "--on-conflict", "overwrite",
                       echo=echo)["item_id"]

    ids = [upload(files[0], echo=True)]
    with ThreadPoolExecutor(6) as pool:  # `--on-conflict overwrite` makes a repeat after a network error harmless
        ids += list(pool.map(upload, files[1:]))
    note(f"uploaded {len(ids)} memos to the Library (names start with {PREFIX}-), 6 at a time")
    t0 = time.time()
    try:
        emit("cmd", shlex.join(["memorylake", "proj", "doc", "import", "--project", world["memos"], *ids[:2]])
             + f" … {len(ids) - 2} more --wait --workspace {cli.workspace}")
        res = cli.run("proj", "doc", "import", "--project", world["memos"], *ids, "--wait", scoped=True, echo=False) or {}
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
        res = {}
    docs = wait_docs(cli, world["memos"], len(ids))
    note(f"{len(docs)} memos indexed in {int(time.time() - t0)} s"
         + (f" ({res.get('success_count')} imported, {res.get('failure_count')} failed)" if res else ""))
    texts = [c["text"] for c in d["citator"]]
    emit("cmd", shlex.join(["memorylake", "fact", "add", "--project", world["citator"], *texts[:1],
                            "--workspace", cli.workspace]) + f"  … {len(texts) - 1} more")
    cli.run("fact", "add", "--project", world["citator"], *texts, scoped=True, echo=False)
    got = list_facts(cli, world["citator"])
    if sorted(f["fact"] for f in got) != sorted(texts):
        die(f"the citator holds {len(got)} notes, expected {len(texts)}")
    note(f"{len(got)} citator notes, one per non-compete case: "
         + ", ".join(f"{c['case'].split(' v. ')[0]} {c['status']}" for c in d["citator"][:3]) + " …")
    emit("memory", "", memos=len(docs), citator=len(got),
         reversed=[c["case"] for c in d["citator"] if c["status"] == "REVERSED"])


def wait_docs(cli: CLI, project: str, n: int) -> list[dict]:
    for attempt in range(60):
        page = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True,
                       echo=attempt == 0) or {}
        docs = page.get("items") or []
        okay = [x for x in docs if x.get("status") == "okay"]
        if len(okay) >= n:
            return okay
        bad = [x for x in docs if x.get("status") == "error"]
        if bad:
            die(f"{len(bad)} memo(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        time.sleep(5)
    die("the memos are still processing after 5 minutes; run again later")


# --------------------------------------------------------------------------- step 4: the memo is whole

def parse_range(r: str) -> int | None:
    m = re.match(r"P(\d+)\[", r or "")
    return int(m.group(1)) if m else None


def memo_pages(cli: CLI, world: dict, case: str, echo: bool = True) -> list[dict]:
    """The pages of one memo as search returns them: page number from `range`, the page's text. Two queries —
    the case name, and the case name with "reversed on appeal" — because a page that never names the case (an
    addendum) does not come back for the name alone."""
    want = case.split(" v. ")[0].split()[0].lower()
    pages: dict[int, dict] = {}
    for k, query in enumerate((case, f"{case} reversed on appeal")):
        res = cli.run("search", query, "--projects", world["memos"], "--types", "document", "--top-k", "4",
                      scoped=True, echo=echo) or {}
        for doc in res.get("documents") or []:
            name = doc.get("document_name") or doc.get("file_name") or ""
            if want not in name.lower():
                continue
            for it in doc.get("items") or []:
                for c in (it.get("highlight") or {}).get("chunks") or []:
                    p = parse_range(c.get("range") or "")
                    if p and p not in pages:
                        pages[p] = dict(page=p, document=name, text=(c.get("text") or "").strip(), query=k + 1)
    return [pages[k] for k in sorted(pages)]


def show_memo(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    t = tarrow(d)
    pages = memo_pages(cli, world, t["case"])
    if not pages:
        die("search returned no pages of the Tarrow memo")
    lines = []
    for p in pages:
        first = " ".join(p["text"].split())
        lines.append(f"    page {p['page']}  {first[:140]}{'…' if len(first) > 140 else ''}"
                     + ("" if p["query"] == 1 else "   ← only the second search"))
    emit("text", f"\n  {pages[0]['document']}: {len(pages)} of {len(t['pages'])} pages, each its own entry in the index\n"
         + "\n".join(lines))
    rev = next((p for p in pages if "Reversed" in p["text"]), None)
    if rev:
        note(f"the reversal is in memory — page {rev['page']} of the same memo, a page that never names the case. "
             "MemoryRouter attaches documents at this same unit: one page per entry, picked by how well that page "
             "matches the question")
    else:
        note("the addendum page did not come back for either search this time; it is in the PDF (see data/firm.json)")
    emit("pages", "", case=t["case"], pages=[dict(page=p["page"], query=p["query"], text=p["text"][:400]) for p in pages])
    return pages


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


def answer(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    d = load_data()
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "260", "--temperature", "0",
                            "--body", str(write_body().relative_to(HERE)), "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, cases=[])
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(text, d))


def check(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is
    given can differ from what the answer call was given."""
    d = load_data()
    res = router_call(cli, ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
                            "--system", d["checker"], "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], readable=False, pages=[], citator=False, entries=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return dict(q=q["id"], **read_attached(text, d))


def read_attached(text: str, d: dict) -> dict:
    """Which Tarrow pages and which citator note the checker says it was given."""
    parts = text.split("```")
    if len(parts) < 3:
        return dict(readable="NONE" in text, pages=[], citator=False, entries=0)
    block = parts[1]
    entries = re.split(r"\n- ", "\n" + block)[1:]
    if not entries and "NONE" not in block:  # copied the instruction but not the memory block: no reading
        return dict(readable=False, pages=[], citator=False, entries=0)
    marks = page_marks(d)
    pages, cit, order = set(), False, []
    for e in entries:
        m = re.match(r"\(([^)]+)\)\s*(.*)", e, re.S)
        if m and m.group(1).endswith(tarrow(d)["file"]):
            body = " ".join(m.group(2).split())
            for i, mk in enumerate(marks, 1):
                if mk in body:
                    pages.add(i)
                    order.append(i)
        elif not m and "Citator note" in e and "Tarrow" in e:
            cit = True
    return dict(readable=True, pages=sorted(pages), order=order, citator=cit, entries=len(entries))


def count(cli: CLI, prompt: int, reply: int) -> None:
    with LOCK:
        cli.tokens["prompt"] += prompt  # type: ignore[attr-defined]
        cli.tokens["completion"] += reply  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]


def write_body() -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / "body.json"
    if not path.exists():
        body = {"model": MODEL, "messages": [{"role": "system", "content": load_data()["system"]}]}
        path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def attached_label(c: dict) -> str:
    if not c["readable"]:
        return "unreadable"
    p = c.get("order") or c["pages"]
    s = ("→".join(f"p{x}" for x in p) if p else "no Tarrow page")
    return s + (" +citator" if c["citator"] else "")


# --------------------------------------------------------------------------- steps 5 and 6

def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}")
        first = answer(cli, q, boundary, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        if n == 0:
            emit("text", "    first answer:\n      " + "\n      ".join(textwrap.wrap(first["reply"], 100)[:6]))
        emit("text", f"    answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     f"(✗ cites Tarrow as good law · ✓ says it was reversed · · does not use Tarrow)")
        emit("text", f"    checker, what was attached: {' · '.join(attached_label(c) for c in checks)}   "
                     f"(Tarrow pages in the order attached: p1 holding … p4 addendum)")
        bad = next((a for a in answers if a["verdict"] == "bad"), None)
        if bad and arm == "memos":
            line = next((s for s in re.split(r"(?<! v\.)(?<=[.!?])\s+", bad["reply"]) if "Tarrow" in s), bad["reply"])
            emit("text", "    ✗ e.g. “" + " ".join(line.split())[:220] + "”")
        row = dict(q=q["id"], arm=arm, answers=answers, checks=checks)
        rows.append(row)
        emit("asked", "", q=q["id"], arm=arm,
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers],
             checks=[dict(label=attached_label(c), **c) for c in checks])
    return rows


def tally(rows: list[dict]) -> dict:
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    chk = [c for r in rows for c in r["checks"] if c["readable"]]
    return dict(answers=len(ans), bad=sum(a["verdict"] == "bad" for a in ans),
                flagged=sum(a["verdict"] == "flagged" for a in ans), none=sum(a["verdict"] == "none" for a in ans),
                checks=len(chk), holding=sum(1 in c["pages"] for c in chk),
                addendum=sum(4 in c["pages"] for c in chk),
                holding_alone=sum(1 in c["pages"] and 4 not in c["pages"] and not c["citator"] for c in chk),
                addendum_first=sum(1 in c["pages"] and 4 in c["pages"] and (c.get("order") or [0]).index(4) < (c.get("order") or [0, 0]).index(1)
                                   if 1 in (c.get("order") or []) and 4 in (c.get("order") or []) else False for c in chk),
                citator=sum(c["citator"] for c in chk))


def tally_line(t: dict) -> str:
    return (f"{t['bad']}/{t['answers']} answers cite Tarrow as good law, {t['flagged']} say it was reversed, "
            f"{t['none']} do not use it  ·  checker: holding page attached {t['holding']}/{t['checks']}, "
            f"addendum {t['addendum']}/{t['checks']} (before the holding page {t['addendum_first']}), citator note {t['citator']}/{t['checks']}")


# --------------------------------------------------------------------------- step 7: control

ROUTER_PREAMBLE = ("The following information was remembered about this user from earlier conversations, or retrieved "
                   "from documents they have stored. It may or may not be relevant to the current message. Use an entry "
                   "only if it genuinely helps answer what the user is asking now; silently ignore anything irrelevant, "
                   "and never repeat an entry back just because it is here.")
# (name, entries in order, label). "m:<short>" is page 1 of another memo, as MemoryRouter interleaves other matches.
CONTROL = (("p1", [1], "holding page only"),
           ("p1p4", [1, 4], "holding, then addendum"),
           ("p4xp1", [4, "m:Corvid", "m:Cascade", "m:Brightwater", 1], "addendum, 3 other memos, holding"))
CONTROL_RUNS = 2


def control(cli: CLI, pages: list[dict]) -> dict:
    """The same questions with the Tarrow pages handed over directly (`router chat --body`, no boundary), laid out
    the way MemoryRouter lays out attached pages. Only which pages are there, and in what order, changes."""
    d = load_data()
    out = {}
    by_page = {p["page"]: p for p in pages}
    others = {m["short"]: m for m in d["memos"]}
    for name, sel, label in CONTROL:
        if any(isinstance(x, int) and x not in by_page for x in sel):
            note(f"skipping “{label}”: step 4 did not return every page it needs")
            continue
        lines = []
        for x in sel:
            if isinstance(x, int):
                lines.append(f"- ({by_page[x]['document']}) {by_page[x]['text']}")
            else:
                m = others[x[2:]]
                lines.append(f"- ({lib_name(m['file'])}) Precedent memo: {m['case']}\n{m['court']}, decided {m['decided']}"
                             f" - memo by the employment group\n1. Holding\n{m['holding']}\n2. Facts\n{m['facts']}")
        entries = "\n".join(lines)
        path = OUT / f"body-{name}.json"
        path.write_text(json.dumps({"model": MODEL, "messages": [{"role": "system", "content": d["system"] + "\n\n"
                                    + ROUTER_PREAMBLE + "\n" + entries}]}, ensure_ascii=False, indent=1), encoding="utf-8")

        def one(q: dict, echo: bool = False) -> dict:
            res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "260", "--temperature", "0",
                                    "--body", str(path.relative_to(HERE))], echo)
            if not res:
                return dict(q=q["id"], verdict=None, reply="")
            u = res.get("usage") or {}
            count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            text = (res["choices"][0]["message"]["content"] or "").strip()
            return dict(q=q["id"], reply=text, **grade(text, d))

        plan = [q for q in d["questions"] for _ in range(CONTROL_RUNS)]
        first = one(plan[0], echo=name == "p1")
        with ThreadPoolExecutor(6) as pool:
            got = [first] + list(pool.map(one, plan[1:]))
        if not cli.router_ok:  # type: ignore[attr-defined]
            return out
        out[name] = got
        by_q = "   ".join(f"{q['id']} " + " ".join(MARK[g["verdict"]] for g in got if g["q"] == q["id"]) for q in d["questions"])
        bad = sum(g["verdict"] == "bad" for g in got)
        emit("text", f"    {label:<34} {by_q}    → {bad}/{len(got)} cite Tarrow as good law")
        emit("control", "", variant=name, label=label, pages=sel,
             answers=[dict(q=g["q"], verdict=g["verdict"], reply=g["reply"]) for g in got])
    return out


# --------------------------------------------------------------------------- step 8: citator check

def citator_status(cli: CLI, world: dict, case: str, cache: dict, echo: bool = False) -> dict:
    if case not in cache:
        notes = list_facts(cli, world["citator"], echo=echo, query=case)
        rev = next((f for f in notes if "REVERSED" in f["fact"]), None)
        cache[case] = dict(status="REVERSED" if rev else ("good law" if notes else "no note"),
                           note=(rev or (notes[0] if notes else {})).get("fact", ""))
    return cache[case]


def citator_check(cli: CLI, world: dict, text: str, cache: dict, echo: bool = False) -> dict:
    d = load_data()
    cases = cited_cases(text, d)
    status = {c: citator_status(cli, world, c, cache, echo=echo) for c in cases}
    hits = [c for c in cases if status[c]["status"] == "REVERSED"]
    flagged = all(knows_appeal(text, next(m["short"] for m in d["memos"] if m["case"] == c)) for c in hits)
    return dict(cases=cases, status={c: s["status"] for c, s in status.items()}, reversed=hits,
                held=bool(hits) and not flagged, note=status[hits[0]]["note"] if hits else "")


def run_citator(cli: CLI, world: dict, rows: list[dict]) -> dict:
    cache: dict = {}
    t = tarrow(load_data())["case"]
    citator_status(cli, world, t, cache, echo=True)
    emit("text", f"    {t}: {cache[t]['status']} — “{cache[t]['note']}”")
    out = {"memos": [], "citator": []}
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] is None:
                continue
            c = citator_check(cli, world, a["reply"], cache)
            out[r["arm"]].append(dict(q=r["q"], verdict=a["verdict"], held=c["held"], cases=c["cases"]))
    for arm, label in ARMS:
        got = out[arm]
        bad = [g for g in got if g["verdict"] == "bad"]
        held = [g for g in got if g["held"]]
        emit("text", f"    {label:<16} {len(held)} of {len(got)} answers held back (they lean on a reversed case and do not say so)"
                     f"  ·  ✗ in step {5 if arm == 'memos' else 6}: {len(bad)}, all held: {'yes' if all(g['held'] for g in bad) else 'NO'}")
    looked = ", ".join(f"{c.split(' v. ')[0]} {s['status']}" for c, s in cache.items())
    note(f"cases looked up: {looked} — `fact list --query` is an exact substring match on the note's text, "
         "so the note must spell the case name the way answers do")
    emit("citator_check", "", **{k: dict(answers=len(v), held=sum(g["held"] for g in v),
                                         bad=sum(g["verdict"] == "bad" for g in v)) for k, v in out.items()})
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 9
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a memo project, a citator project, two boundaries")
    world = setup(cli)

    banner(3, TOTAL, f"What the firm knows — {len(d['memos'])} precedent memos (PDF) and {len(d['citator'])} citator notes")
    store(cli, world)

    banner(4, TOTAL, "The Tarrow memo is all in memory — page by page")
    pages = show_memo(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, (arm, label) in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — boundary: {label}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) {CHECKS}× copies out what it was given")
        got = ask_all(cli, world, arm)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {label}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Control — hand the model the Tarrow pages yourself, no boundary")
    note(f"`router chat --body` with the pages in the system prompt, laid out as MemoryRouter lays them out; "
         f"each question {CONTROL_RUNS}×. Only which pages are there, and in what order, changes")
    ctl = control(cli, pages) if cli.router_ok else {}  # type: ignore[attr-defined]

    banner(8, TOTAL, "Citator check — look up every case an answer cites")
    cit = run_citator(cli, world, rows) if rows else {}

    banner(9, TOTAL, "Scoreboard")
    if tallies:
        for arm, label in ARMS:
            if arm in tallies:
                emit("text", f"  {label:<16} {tally_line(tallies[arm])}")
        for name, sel, label in CONTROL:
            if name in ctl:
                bad = sum(g["verdict"] == "bad" for g in ctl[name])
                emit("text", f"  {label:<34} (no boundary) {bad}/{len(ctl[name])} cite Tarrow as good law")
        a = tallies.get("memos", {})
        if a:
            emit("text", f"\n  Through the memos alone the checker saw the holding page without the addendum "
                         f"{a['holding_alone']} time(s) in {a['checks']}, and the addendum before the holding page "
                         f"{a['addendum_first']} time(s). In the control, the holding page alone and an addendum placed before "
                         "it both lead to the reversed ruling being cited; the addendum after it does not. The citator "
                         "check after the model holds back every answer that leans on a reversed case, whatever was attached.")
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, control=ctl, citator=cit, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer, checker reading and lookup: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def find_case(d: dict, name: str) -> dict:
    for m in d["memos"]:
        if name.lower() in m["case"].lower():
            return m
    die(f"no memo for {name!r}; one of: {', '.join(m['short'] for m in d['memos'])}")


def show_pages(cli: CLI, name: str) -> list[dict]:
    """The pages of one memo as the index holds them."""
    d = load_data()
    m = find_case(d, name)
    world = load_world(cli)
    pages = memo_pages(cli, world, m["case"])
    emit("text", f"\n  {m['case']} — {len(pages)} page(s) in the index")
    for p in pages:
        emit("text", f"\n  page {p['page']}\n    " + "\n    ".join(textwrap.wrap(" ".join(p["text"].split()), 100)))
    emit("pages", "", case=m["case"], pages=[dict(page=p["page"], text=p["text"][:400]) for p in pages])
    return pages


def cite(cli: CLI, text: str) -> dict:
    """Run the citator check on any text: a draft paragraph, an answer pasted from elsewhere."""
    world = load_world(cli)
    c = citator_check(cli, world, text, {}, echo=True)
    if not c["cases"]:
        emit("text", "\n  no case from the memo library is named in that text")
    for case, s in c["status"].items():
        emit("text", f"  {'✗' if s == 'REVERSED' else '✓'} {case}: {s}")
    if c["reversed"]:
        emit("text", f"  note: {c['note']}")
        emit("text", "  → hold: the text leans on a reversed case and does not say so" if c["held"]
             else "  → the text names the reversal; fine to send")
    emit("cite", "", **c)
    return c


# --------------------------------------------------------------------------- cleanup

def delete_fact(cli: CLI, fact_id: str, project: str) -> None:
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, "--project", project, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_facts = n_docs = 0
    for name in ("citator", "memos"):
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        left = list_facts(cli, proj["id"], echo=False)
        n_facts += len(left)
        for attempt in range(4):  # a list right after deletes can still show a few: delete those and look again
            for f in left:
                delete_fact(cli, f["id"], proj["id"])
            time.sleep(2 * attempt)
            left = list_facts(cli, proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in {name} after deleting; run cleanup again")
        if name == "memos":
            n_docs = len((cli.run("proj", "doc", "list", "--project", proj["id"], "--page-size", "100",
                                  scoped=True, echo=False) or {}).get("items") or [])
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundaries, both projects ({n_docs} memo document(s), {n_facts} citator note(s)) "
             f"and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "pages", "cite", "cleanup"],
                    help="run = full demo (default); pages <case> = one memo's pages in the index; "
                         "cite \"<text>\" = citator check on any text; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for pages: a case name (Tarrow, Corvid …); for cite: the text")
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
    if args.command in ("pages", "cite"):
        if not args.args:
            die(f"usage: python3 demo.py {args.command} " + ("<case>" if args.command == "pages" else '"<text>"'))
        text = " ".join(args.args)
        if args.command == "pages":
            banner(2, 2, f"The pages of the {text} memo in the index")
            show_pages(cli, text)
        else:
            banner(2, 2, "Citator check")
            cite(cli, text)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py pages Tarrow` or "
          "`python3 demo.py cite \"Tarrow Freight v. Delling voids it for lack of notice.\"`; "
          "`python3 demo.py cleanup` removes the demo data.")


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

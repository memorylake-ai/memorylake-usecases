#!/usr/bin/env python3
"""report-table-memory — a research desk's IR reports behind MemoryRouter, and the comma that is a decimal point.

Kestrel Ridge Capital (a fictional fund) covers European industrials. The desk keeps every covered company's
quarterly IR report (PDF) in a MemoryLake project, and the research assistant is an ordinary model call through
MemoryRouter with `--boundary`: MemoryRouter attaches what it finds relevant, documents included.

MemoryRouter attaches a PDF table as comma-separated text with nothing quoted. A German, Italian or Dutch report
prints 4,5 for four and a half, so the row "Lieferzeit (Tage) | 12 | 4,5" reaches the model as
"Lieferzeit (Tage),12,4,5" — which is 12 and 4.5, or 12.4 and 5. Both splits are valid. The page and the row
are attached; what is lost is where one cell ends and the next begins.

The demo shows:
  · the table in the index (`search --types document`), as the PDF and as an .xlsx of the same numbers; every
    row of all six reports split every way its number format allows — before anything is asked;
  · eight questions a PM asks, through a boundary over the PDFs and through one over the .xlsx exports, each
    answered several times; a checker call (Claude copies out what it was given) shows the row as attached;
  · a control without MemoryRouter: the same table handed over directly in MemoryRouter's layout, once as the
    index wrote it and once with the cell boundaries kept — only the separator changes;
  · a deterministic number check before an answer goes out: count the readings of the row it came from; hold
    answers on rows that read two ways and take those from the model export.

What MemoryRouter attaches varies from call to call; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (112 calls per run, roughly 50,000 tokens).
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

PREFIX = "mlu-rtm"  # memorylake-usecases / report-table-memory


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



MODEL = "gpt-4o-mini"             # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 4                          # answers per question per boundary
CHECKS = 1                        # checker calls per question per boundary
CONTROL_RUNS = 2                  # answers per question per layout in the control
ARMS = (("pdf", "IR reports (PDF)"), ("xlsx", "model exports (.xlsx)"))
ROUTER_PREAMBLE = ("The following information was remembered about this user from earlier conversations, or retrieved "
                   "from documents they have stored. It may or may not be relevant to the current message. Use an entry "
                   "only if it genuinely helps answer what the user is asking now; silently ignore anything irrelevant, "
                   "and never repeat an entry back just because it is here.")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "desk.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def company(d: dict, name: str) -> dict:
    for c in d["companies"]:
        if name.lower() in (c["short"].lower(), c["company"].lower()) or name.lower() in c["company"].lower():
            return c
    die(f"no company {name!r}; one of: {', '.join(c['short'] for c in d['companies'])}")


def question(d: dict, qid: str) -> dict:
    for q in d["questions"]:
        if q["id"].lower() == qid.lower():
            return q
    die(f"unknown question {qid!r}; one of: {', '.join(q['id'] for q in d['questions'])}")


def printed_number(s: str, convention: str) -> float:
    """A cell as the company prints it, as a number: "1.204" / "412,6" (de) or "1,204" / "154.2" (en)."""
    s = s.replace(".", "").replace(",", ".") if convention == "de" else s.replace(",", "")
    v = float(s)
    return int(v) if v.is_integer() else v


def truth(d: dict, q: dict) -> list[float]:
    c = company(d, q["company"])
    row = next(r for r in c["rows"] if r["label"] == q["row"])
    return [printed_number(v, c["convention"]) for v in row["cells"]]


# --------------------------------------------------------------------------- how many ways a row reads

DE_INT = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")   # 2.140 or 95
EN_FIRST = re.compile(r"\d{1,3}(?:\.\d+)?|\d+(?:\.\d+)?")
EN_GROUP = re.compile(r"\d{3}(?:\.\d+)?")


def _value(tokens: list[str], convention: str) -> float | None:
    """Comma-separated tokens read as one number in the document's convention, or None."""
    if convention == "de":  # comma = decimal point: an integer part, then at most one decimal part
        if len(tokens) == 1 and DE_INT.fullmatch(tokens[0]):
            return printed_number(tokens[0], "de")
        if len(tokens) == 1 and re.fullmatch(r"\d+\.\d{1,2}", tokens[0]):  # the index rewrote 92,7 as 92.7
            return float(tokens[0])
        if len(tokens) == 2 and DE_INT.fullmatch(tokens[0]) and re.fullmatch(r"\d+", tokens[1]):
            return printed_number(",".join(tokens), "de")
        return None
    # en: comma = thousands separator: 1-3 digits, then groups of exactly three; a decimal only at the end
    if len(tokens) == 1:
        return printed_number(tokens[0], "en") if re.fullmatch(r"\d+(?:\.\d+)?", tokens[0]) else None
    if not re.fullmatch(r"\d{1,3}", tokens[0]):
        return None
    if not all(re.fullmatch(r"\d{3}", t) for t in tokens[1:-1]) or not EN_GROUP.fullmatch(tokens[-1]):
        return None
    return printed_number(",".join(tokens), "en")


def readings(tokens: list[str], n: int, convention: str) -> list[list[float]]:
    """Every way to cut the tokens of a CSV row (label removed) into n numbers in the document's convention."""
    if n == 0:
        return [[]] if not tokens else []
    out = []
    for k in range(1, len(tokens) - n + 2):
        v = _value(tokens[:k], convention)
        if v is not None:
            out += [[v] + rest for rest in readings(tokens[k:], n - 1, convention)]
    return out


def csv_row(chunk: str, label: str) -> str | None:
    """The line of an indexed table chunk that holds this row, as the index wrote it."""
    for line in (chunk or "").splitlines():
        if line.startswith(label + ","):
            return line
    return None


def row_readings(line: str, label: str, ncols: int, convention: str) -> list[list[float]]:
    return readings(line[len(label) + 1:].split(","), ncols - 1, convention)


def fmt(v: float) -> str:
    return f"{v:,}" if isinstance(v, int) else f"{v:,.3f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- grading

# Numbers in a reply, in English: 1,204 · 102.3 · 4,5 (a German-style decimal, if the model copies it) · 1.204 billion.
NUM = re.compile(r"(?<![A-Za-z0-9.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+,\d{1,2}(?![\d])|\d+(?:\.\d+)?)"
                 r"(?![A-Za-z0-9])(\s*(?:billion|bn))?", re.I)


def reply_numbers(text: str) -> set[float]:
    out = set()
    for m in NUM.finditer(text or ""):
        s = m.group(1)
        if re.fullmatch(r"\d+,\d{1,2}", s):
            v = float(s.replace(",", "."))
        else:
            v = float(s.replace(",", ""))
        if m.group(2):
            v *= 1000
        out.add(round(v, 4))
    return out


def grade(reply: str, expect: list[float], alt: list[list[float]]) -> str:
    """right = every true figure is in the reply; wrong = a figure only another reading of the row gives;
    none = neither (no figure, or "not given")."""
    nums = reply_numbers(reply)
    if all(round(float(v), 4) in nums for v in expect):
        return "right"
    others = {round(float(v), 4) for r in alt for v in r} - {round(float(v), 4) for v in expect}
    return "wrong" if nums & others or nums - {2026.0} else "none"


MARK = {"right": "✓", "wrong": "✗", "none": "·", None: "–"}


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
    if demo_boundaries(cli) or find_project(cli, "pdf") or find_project(cli, "xlsx") or demo_files(cli):
        note("an earlier run left the desk's memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    pdf = cli.run("proj", "create", "--name", f"{d['fund']} — IR reports (PDF)", "--custom-id", cid("pdf"),
                  "--description", "Quarterly IR reports of the covered companies, as published. Demo data.", scoped=True)
    xlsx = cli.run("proj", "create", "--name", f"{d['fund']} — model exports (.xlsx)", "--custom-id", cid("xlsx"),
                   "--description", "The same KPI tables, keyed into the desk's models. Demo data.", scoped=True)
    a = cli.run("boundary", "create", "--name", "Research assistant: IR reports", "--custom-id", cid("pdf"),
                "--projects", pdf["id"], scoped=True)
    b = cli.run("boundary", "create", "--name", "Research assistant: model exports", "--custom-id", cid("xlsx"),
                "--projects", xlsx["id"], scoped=True)
    note(f"boundary {a['id']} covers the PDF reports; boundary {b['id']} covers the .xlsx exports of the same tables. "
         "Every answer is `router chat … --boundary` and MemoryRouter picks what to attach")
    world = dict(pdf=pdf["id"], xlsx=xlsx["id"], boundary={"pdf": a["id"], "xlsx": b["id"]})
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    pdf, xlsx = find_project(cli, "pdf"), find_project(cli, "xlsx")
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    if not pdf or not xlsx or cid("pdf") not in bs or cid("xlsx") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(pdf=pdf["id"], xlsx=xlsx["id"], boundary={"pdf": bs[cid("pdf")], "xlsx": bs[cid("xlsx")]})


# --------------------------------------------------------------------------- step 3: the sources

def import_files(cli: CLI, project: str, files: list[Path], label: str) -> int:
    def upload(f: Path, echo: bool = False) -> str:
        return cli.run("lib", "upload", os.path.relpath(f), "--name", lib_name(f.name), "--on-conflict", "overwrite",
                       echo=echo)["item_id"]

    ids = [upload(files[0], echo=True)]
    with ThreadPoolExecutor(6) as pool:  # `--on-conflict overwrite` makes a repeat after a network error harmless
        ids += list(pool.map(upload, files[1:]))
    t0 = time.time()
    try:
        emit("cmd", shlex.join(["memorylake", "proj", "doc", "import", "--project", project, *ids[:1]])
             + f" … {len(ids) - 1} more --wait --workspace {cli.workspace}")
        cli.run("proj", "doc", "import", "--project", project, *ids, "--wait", scoped=True, echo=False)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    docs = wait_docs(cli, project, len(ids))
    note(f"{len(docs)} {label} uploaded and indexed in {int(time.time() - t0)} s")
    return len(docs)


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    pdfs = [DATA / "reports" / c["pdf"] for c in d["companies"]]
    xlsxs = [DATA / "models" / c["xlsx"] for c in d["companies"]]
    missing = [p.name for p in pdfs + xlsxs if not p.exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_reports.py`")
    emit("text", "\n  " + "\n  ".join(f"{c['country']}  {c['company']:<30} prints {'412,6 · 2.140' if c['convention'] == 'de' else '154.2 · 2,310'}"
                                      for c in d["companies"]))
    n_pdf = import_files(cli, world["pdf"], pdfs, "IR reports (PDF)")
    n_xlsx = import_files(cli, world["xlsx"], xlsxs, "model exports (.xlsx)")
    emit("memory", "", pdf=n_pdf, xlsx=n_xlsx, companies=[dict(company=c["company"], country=c["country"],
                                                              convention=c["convention"]) for c in d["companies"]])


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
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        time.sleep(5)
    die("the documents are still processing after 5 minutes; run again later")


# --------------------------------------------------------------------------- step 4: the table in the index

def table_chunk(cli: CLI, project: str, c: dict, kind: str, echo: bool = False) -> str | None:
    """The table of one company's document as the index holds it — the text MemoryRouter attaches."""
    want = lib_name(c[kind]).lower()
    for query in (f"{c['company']} {c['caption']}", f"{c['short']} {' '.join(r['label'] for r in c['rows'][:3])}"):
        res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "6",
                      scoped=True, echo=echo) or {}
        echo = False
        for doc in res.get("documents") or []:
            name = (doc.get("document_name") or doc.get("file_name") or "").lower()
            if name != want:
                continue
            for it in doc.get("items") or []:
                if it.get("type") == "table":
                    text = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or [])
                    if text.strip():
                        return text.strip()
    return None


def index_rows(cli: CLI, world: dict, c: dict, echo: bool = False) -> list[dict]:
    """Each row of a company's PDF table as indexed, with the readings it allows."""
    chunk = table_chunk(cli, world["pdf"], c, "pdf", echo=echo)
    out = []
    for r in c["rows"]:
        line = csv_row(chunk or "", r["label"])
        rd = row_readings(line, r["label"], len(c["columns"]), c["convention"]) if line else []
        out.append(dict(label=r["label"], printed=r["cells"], line=line, readings=rd))
    return out


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    v = company(d, "Vossberg")
    pdf_chunk = table_chunk(cli, world["pdf"], v, "pdf", echo=True)
    xlsx_chunk = table_chunk(cli, world["xlsx"], v, "xlsx", echo=True)
    if not pdf_chunk:
        die("search did not return the Vossberg table from the PDF; run again in a minute")
    emit("text", f"\n  {lib_name(v['pdf'])} — the table as the index holds it (and as MemoryRouter attaches it):\n"
         + "\n".join("    " + ln for ln in pdf_chunk.splitlines()))
    if xlsx_chunk:
        emit("text", f"\n  {lib_name(v['xlsx'])} — the same table from the .xlsx:\n"
             + "\n".join("    " + ln for ln in xlsx_chunk.splitlines()))
    note("the PDF table comes back as comma-separated values with nothing quoted — and in a German report the "
         "comma is also the decimal point. Where a row has two ways to split, both are valid")

    table = {c["short"]: index_rows(cli, world, c) for c in d["companies"]}
    emit("cmd", f"(the same search for the other {len(d['companies']) - 1} companies; each row split every way "
                "the company's number format allows)")
    lines, two, rows_n, rewritten = [], [], 0, []
    for c in d["companies"]:
        for r in table[c["short"]]:
            rows_n += 1
            if not r["line"]:
                lines.append(f"    {c['short']:<10} {r['label']:<30} (row not in the returned chunk)")
                continue
            n = len(r["readings"])
            if n > 1:
                two.append(f"{c['short']} · {r['label']}")
            ways = "   or   ".join(" | ".join(fmt(x) for x in rd) for rd in r["readings"]) or "?"
            redone = r["line"] != ",".join([r["label"], *r["printed"]])
            if redone:
                rewritten.append(c["short"])
            lines.append(f"    {'⚠' if n > 1 else ' '} {c['short']:<9} {r['line']:<40} {n} reading{'s' if n != 1 else ''}: {ways}"
                         + ("   (index wrote . for ,)" if redone else ""))
    emit("text", "\n" + "\n".join(lines))
    note(f"{len(two)} of {rows_n} rows read two ways in the index; the rest read one way. Nothing has been asked yet")
    if rewritten:
        note(f"the index wrote decimal points into {', '.join(sorted(set(rewritten)))}'s table this time (the PDF prints "
             "commas), which removes the clash for those rows; it did not for the others")
    emit("index", "", pdf_chunk=pdf_chunk, xlsx_chunk=xlsx_chunk or "",
         rows=[dict(company=c["short"], **r) for c in d["companies"] for r in table[c["short"]]])
    return table


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



def write_body() -> Path:
    """The request body every answer call uses. Written once, atomically: answer calls run 6 at a time, and a
    file being rewritten under a concurrent reader is read as empty."""
    path = OUT / "body.json"
    text = json.dumps({"model": MODEL, "messages": [{"role": "system", "content": load_data()["system"]}]},
                      ensure_ascii=False, indent=1)
    with LOCK:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            OUT.mkdir(exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
    return path


def answer(cli: CLI, q: dict, boundary: str, alt: list, echo: bool = False) -> dict:
    d = load_data()
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "160", "--temperature", "0",
                            "--body", str(write_body().relative_to(HERE)), "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None)
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), verdict=grade(text, truth(d, q), alt))


def check(cli: CLI, q: dict, boundary: str, kind: str, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given, and find the asked-about row
    in it. Its own call: what it is given can differ from what the answer call was given."""
    d = load_data()
    res = router_call(cli, ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
                            "--system", d["checker"], "--boundary", boundary], echo)
    if not res:
        return dict(readable=False, row=None, declined=False, raw="")
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(text, company(d, q["company"]), q["row"], kind)


def read_attached(text: str, c: dict, label: str, kind: str) -> dict:
    """The asked-about row as the checker copied it out. The copy does not always keep the "- " before each
    entry, so look for the file name and the row anywhere in the copied block. "NONE" is not proof that nothing
    was attached: with the table attached, the checker has written NONE and then answered from the table."""
    parts = text.split("```")
    raw = text[:1500]
    if len(parts) < 3:
        return dict(readable=False, row=None, declined=False, raw=raw)
    block = parts[1]
    if block.strip() == "NONE":
        return dict(readable=False, row=None, declined=True, raw=raw)
    if "remembered" not in block and lib_name(c[kind]) not in block:  # copied something else: no reading
        return dict(readable=False, row=None, declined=False, raw=raw)
    if lib_name(c[kind]) in block:
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("- ("):
                line = line.split(") ", 1)[-1]
            elif line.startswith("("):
                line = line.split(") ", 1)[-1]
            if line.startswith(label + ",") or f": {label}," in line:
                return dict(readable=True, row=line, declined=False, raw=raw)
    return dict(readable=True, row=None, declined=False, raw=raw)


def ask_all(cli: CLI, world: dict, arm: str, table: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        r = next(x for x in table[q["company"]] if x["label"] == q["row"])
        alt = r["readings"]
        emit("text", f"\n  {q['id']}  {q['say']}"
                     f"\n      in the PDF index: {r['line'] or '(row not returned)'}   "
                     f"→ {len(alt)} reading{'s' if len(alt) != 1 else ''}; printed {' | '.join(r['printed'])}")
        first = answer(cli, q, boundary, alt, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary, alt) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, arm, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        seen = next((c["row"] for c in checks if c.get("row")), None)
        state = (seen or ("not attached" if any(c["readable"] for c in checks)
                          else "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"))
        emit("text", f"      checker, the row as attached: {state}")
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "  e.g. “" + " ".join((next((a for a in answers if a["verdict"] == "wrong"), answers[0])["reply"]).split())[:150] + "”")
        row = dict(q=q["id"], arm=arm, ways=len(alt), answers=answers, checks=checks)
        rows.append(row)
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], company=q["company"], label=q["row"], ways=len(alt),
             line=r["line"], printed=r["printed"], attached=state,
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out = {}
    for key, sel in (("two", lambda r: r["ways"] > 1), ("one", lambda r: r["ways"] == 1)):
        ans = [a for r in rows if sel(r) for a in r["answers"] if a["verdict"]]
        out[key] = dict(answers=len(ans), right=sum(a["verdict"] == "right" for a in ans),
                        wrong=sum(a["verdict"] == "wrong" for a in ans), none=sum(a["verdict"] == "none" for a in ans))
    chk = [c for r in rows for c in r["checks"] if c["readable"]]
    out["checks"] = dict(readable=len(chk), attached=sum(bool(c["row"]) for c in chk),
                         declined=sum(bool(c.get("declined")) for r in rows for c in r["checks"]))
    return out


def tally_line(t: dict) -> str:
    a, b = t["two"], t["one"]
    return (f"rows that read two ways: {a['right']}/{a['answers']} right, {a['wrong']} wrong  ·  "
            f"rows that read one way: {b['right']}/{b['answers']} right, {b['wrong']} wrong  ·  "
            f"checker found the row attached {t['checks']['attached']}/{t['checks']['readable']}"
            + (f" ({t['checks']['declined']} declined to copy)" if t["checks"].get("declined") else ""))


# --------------------------------------------------------------------------- step 7: control

def control(cli: CLI, table: dict) -> dict:
    """The same questions with the company's table handed over directly (`router chat --body`, no boundary), in
    MemoryRouter's own layout — once as the index wrote it, once with the PDF's own cell boundaries ( | ).
    Only the separator changes."""
    d = load_data()
    out = {}
    for name, label in (("csv", "as indexed (commas)"), ("cells", "cell boundaries kept ( | )")):
        def body(q: dict) -> Path:
            c = company(d, q["company"])
            if name == "csv":
                rows = [",".join(c["columns"])] + [r["line"] or f"{r['label']},{','.join(r['printed'])}" for r in table[c["short"]]]
            else:
                rows = [" | ".join(c["columns"])] + [" | ".join([r["label"], *r["cells"]]) for r in c["rows"]]
            path = OUT / f"body-{name}-{c['short'].lower()}.json"
            entry = f"- ({lib_name(c['pdf'])}) " + "\n".join(rows)
            text = json.dumps({"model": MODEL, "messages": [{"role": "system", "content": d["system"] + "\n\n"
                               + ROUTER_PREAMBLE + "\n" + entry}]}, ensure_ascii=False, indent=1)
            with LOCK:  # same file for both runs of a question, possibly at once: write it once, atomically
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(text, encoding="utf-8")
                    os.replace(tmp, path)
            return path

        def one(q: dict, echo: bool = False) -> dict:
            r = next(x for x in table[q["company"]] if x["label"] == q["row"])
            res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "160", "--temperature", "0",
                                    "--body", str(body(q).relative_to(HERE))], echo)
            if not res:
                return dict(q=q["id"], verdict=None, reply="", ways=len(r["readings"]))
            u = res.get("usage") or {}
            count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            text = (res["choices"][0]["message"]["content"] or "").strip()
            return dict(q=q["id"], reply=text, ways=len(r["readings"]), verdict=grade(text, truth(d, q), r["readings"]))

        OUT.mkdir(exist_ok=True)
        plan = [q for q in d["questions"] for _ in range(CONTROL_RUNS)]
        first = one(plan[0], echo=name == "csv")
        with ThreadPoolExecutor(6) as pool:
            got = [first] + list(pool.map(one, plan[1:]))
        if not cli.router_ok:  # type: ignore[attr-defined]
            return out
        out[name] = got
        by_q = "  ".join(f"{q['id']} " + "".join(MARK[g["verdict"]] for g in got if g["q"] == q["id"]) for q in d["questions"])
        wrong = sum(g["verdict"] == "wrong" for g in got)
        emit("text", f"    {label:<28} {by_q}   → {wrong}/{len(got)} wrong")
        emit("control", "", variant=name, label=label,
             answers=[dict(q=g["q"], verdict=g["verdict"], reply=g["reply"], ways=g["ways"]) for g in got])
    return out


# --------------------------------------------------------------------------- step 8: the number check

def guard(rows: list[dict], table: dict) -> dict:
    """Before an answer drawn from the PDF reports goes out: look up the row it came from in the index and count
    its readings. One reading → send. Two → hold, and answer from the model export instead."""
    d = load_data()
    pdf = [r for r in rows if r["arm"] == "pdf"]
    xl = {r["q"]: r for r in rows if r["arm"] == "xlsx"}
    held = sent = wrong_sent = wrong_held = 0
    lines = []
    for r in pdf:
        q = question(d, r["q"])
        ans = [a for a in r["answers"] if a["verdict"]]
        wrong = sum(a["verdict"] == "wrong" for a in ans)
        if r["ways"] > 1:
            held += len(ans)
            wrong_held += wrong
            x = xl.get(r["q"])
            fix = (f"model export: {sum(a['verdict'] == 'right' for a in x['answers'] if a['verdict'])}/"
                   f"{sum(1 for a in x['answers'] if a['verdict'])} right") if x else "model export not asked"
            lines.append(f"    hold  {r['q']} {q['company']:<9} {q['row']:<28} {wrong}/{len(ans)} wrong  → {fix}")
        else:
            sent += len(ans)
            wrong_sent += wrong
            lines.append(f"    send  {r['q']} {q['company']:<9} {q['row']:<28} {wrong}/{len(ans)} wrong")
    emit("text", "\n".join(lines))
    emit("text", f"\n    held {held} answers on rows that read two ways ({wrong_held} of them wrong); "
                 f"sent {sent} on rows that read one way ({wrong_sent} of them wrong)")
    note("the check judges the row, not the answer: it cannot tell which answers on a two-way row came out right, "
         "so it holds them all and asks the model export instead")
    out = dict(held=held, wrong_held=wrong_held, sent=sent, wrong_sent=wrong_sent)
    emit("guard", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 9
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for the IR reports, one for the model exports, a boundary each")
    world = setup(cli)

    banner(3, TOTAL, f"What the desk keeps — {len(d['companies'])} quarterly IR reports (PDF) and the same tables as .xlsx")
    store(cli, world)

    banner(4, TOTAL, "The tables in the index — and how many ways each row reads")
    table = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, (arm, label) in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — boundary: {label}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) copies out what it was given")
        got = ask_all(cli, world, arm, table)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {label}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Control — hand the model the table yourself, no boundary")
    note(f"`router chat --body` with MemoryRouter's own preamble and one entry for the company's table, each question "
         f"{CONTROL_RUNS}×: once as the index wrote it, once with the cell boundaries kept. Only the separator changes")
    ctl = control(cli, table) if cli.router_ok else {}  # type: ignore[attr-defined]

    banner(8, TOTAL, "Number check — count a row's readings before its answer goes out")
    note("deterministic, no model: the row the answer is about, as the index holds it, split every way the "
         "company's number format allows")
    grd = guard(rows, table) if rows else {}

    banner(9, TOTAL, "Scoreboard")
    if tallies:
        for arm, label in ARMS:
            if arm in tallies:
                emit("text", f"  {label:<22} {tally_line(tallies[arm])}")
        for name, label in (("csv", "control, as indexed"), ("cells", "control, cell boundaries")):
            if name in ctl:
                g = ctl[name]
                two = [x for x in g if x["ways"] > 1]
                one = [x for x in g if x["ways"] == 1]
                emit("text", f"  {label:<22} two-way rows {sum(x['verdict'] == 'wrong' for x in two)}/{len(two)} wrong · "
                             f"one-way rows {sum(x['verdict'] == 'wrong' for x in one)}/{len(one)} wrong")
        if grd:
            emit("text", f"  {'number check':<22} held {grd['held']} ({grd['wrong_held']} wrong), "
                         f"sent {grd['sent']} ({grd['wrong_sent']} wrong)")
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, control=ctl, guard=grd, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and checker reading: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies, guard=grd)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_rows(cli: CLI, name: str) -> dict:
    """One company's table as the index holds it, PDF and .xlsx, with the readings of every PDF row."""
    d = load_data()
    c = company(d, name)
    world = load_world(cli)
    rows = index_rows(cli, world, c, echo=True)
    xchunk = table_chunk(cli, world["xlsx"], c, "xlsx", echo=True)
    emit("text", f"\n  {c['company']} ({c['country']}, prints {'412,6' if c['convention'] == 'de' else '154.2'})")
    for r in rows:
        ways = "   or   ".join(" | ".join(fmt(x) for x in rd) for rd in r["readings"]) or "(row not returned)"
        emit("text", f"  {'⚠' if len(r['readings']) > 1 else ' '} {r['line'] or r['label']:<40} printed {' | '.join(r['printed']):<14} reads: {ways}")
    if xchunk:
        emit("text", "\n  from the .xlsx:\n" + "\n".join("    " + ln for ln in xchunk.splitlines()))
    out = dict(company=c["company"], rows=rows, xlsx=xchunk or "")
    emit("rows", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = 0
    for name in ("pdf", "xlsx"):
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        n_docs += len((cli.run("proj", "doc", "list", "--project", proj["id"], "--page-size", "100",
                               scoped=True, echo=False) or {}).get("items") or [])
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundaries, both projects ({n_docs} document(s)) and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "rows", "cleanup"],
                    help="run = full demo (default); rows <company> = one company's table in the index and the "
                         "readings of each row; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for rows: a company (Vossberg, Steinbach, Ferrandi, Dekker, Ashcombe, Marlow)")
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
    if args.command == "rows":
        if not args.args:
            die("usage: python3 demo.py rows <company>")
        banner(2, 2, f"The {args.args[0]} table in the index")
        show_rows(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py rows Vossberg` or `python3 demo.py rows Ashcombe`; "
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

#!/usr/bin/env python3
"""assay-chart-memory — plate-run charts behind MemoryRouter, and the numbers that never made it into memory.

Larkspur Bio (a fictional biotech) runs the KA-7 kinase inhibition assay every other week. After each plate run the
plate reader's software exports an IC50 bar chart, and the assay team saves the chart into its notebook — and into
MemoryLake, so the lab assistant (an ordinary model call through MemoryRouter with `--boundary`) "has the results".

MemoryLake indexes an image by looking at it: a vision summary of the chart and the text it can read off it. That
text is what MemoryRouter attaches for the chart. The bars are not in it. Print the value on every bar and the
summary still names two of them — whichever two its writer chose. Ask the assistant for CMP-106's IC50 and it says
it was not given the figure; ask for the three most potent compounds and it ranks the two it was told about.
Store the plate reader's CSV export next to the chart and MemoryRouter attaches the rows: every answer is right.

The demo shows:
  · what the index keeps from each chart (`search --types document`): the summary, the text read off the image,
    and how many of the chart's eight values are in that text — for the plain charts and the labelled ones;
  · six questions the team asks about the two runs (two look-ups, a comparison, a threshold list, a ranking, a
    change between runs), through three boundaries: the charts as the lab keeps them, the same charts with every
    bar labelled, and the charts plus the CSV exports; each answered several times and graded against the plate
    data — right, a wrong number, an incomplete answer, or no figure; a checker call (Claude copies out what it
    was given) shows what MemoryRouter attached for every question.

What MemoryRouter attaches, and what the summary of a chart says, vary from run to run; the README reports totals
over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (72 calls per run, roughly 40,000 tokens).
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

PREFIX = "mlu-asy"  # memorylake-usecases / assay-chart-memory


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
ARMS = ("charts", "labelled", "exports")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "assay.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def stem(d: dict, run: str) -> str:
    return f"{d['assay'].lower().replace('-', '')}-ic50-{run}"


KIND_SUFFIX = {"chart": ".png", "labelled": "-labelled.png", "csv": ".csv"}


def arm_files(d: dict, arm: str) -> list[str]:
    """The files an arm's project holds, chart(s) first."""
    return [stem(d, r["run"]) + KIND_SUFFIX[k] for k in d["arms"][arm]["files"] for r in d["runs"]]


def all_files(d: dict) -> list[str]:
    out: list[str] = []
    for arm in ARMS:
        out += [f for f in arm_files(d, arm) if f not in out]
    return out


def run_of(d: dict, date: str) -> dict:
    for r in d["runs"]:
        if r["run"] == date.strip():
            return r
    die(f"no plate run {date!r}; one of: {', '.join(r['run'] for r in d['runs'])}")


def run_in_name(name: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name or "")
    return m.group(1) if m else None


def file_kind(name: str) -> str:
    return "csv" if name.endswith(".csv") else "labelled chart" if "labelled" in name else "chart"


def short_file(name: str) -> str:
    """mlu-asy-ka7-ic50-2026-09-12-labelled.png → labelled chart 09-12"""
    run = run_in_name(name) or "?"
    return f"{file_kind(name)} {run[5:]}"


# --------------------------------------------------------------------------- grading

VAL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:nM\b|nanomolar)", re.I)
NUMBER = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)(?![\w-])")
CMP = re.compile(r"\bCMP-?(\d{3})\b")
RANGE = re.compile(r"\bCMP-?(\d{3})\s*(?:to|through|-|–|—)\s*CMP-?(\d{3})\b")
NO_FIGURE = re.compile(r"\b(do not|don't|cannot|can't|unable to|not able to)\b.{0,40}\b(have|provide|determine|see|access|"
                       r"find|rank|list|give|say|tell)\b|\bnot (been )?(given|provided|available|included|specified)\b|"
                       r"\bno (specific )?(data|values|figures|information)\b", re.I)


def same(a: float, b: float) -> bool:
    return abs(a - b) < 0.051


def reply_values(text: str) -> list[float]:
    """IC50s stated in a reply: numbers followed by nM. Dates, compound ids and the 40 nM threshold of the question
    are not values (the threshold is removed by grade())."""
    return [float(x) for x in VAL.findall(text or "")]


def named(text: str) -> list[str]:
    """Compounds a reply names, in order; "CMP-101 to CMP-107" names all seven."""
    out: list[str] = []
    spans = []
    for m in RANGE.finditer(text or ""):
        a, b = int(m.group(1)), int(m.group(2))
        for n in range(min(a, b), max(a, b) + 1):
            out.append(f"CMP-{n}")
        spans.append(m.span())
    for m in CMP.finditer(text or ""):
        if not any(s <= m.start() < e for s, e in spans):
            out.append(f"CMP-{m.group(1)}")
    seen: list[str] = []
    for c in out:
        if c not in seen:
            seen.append(c)
    return seen


def lookup(d: dict, pairs: list[list[str]]) -> list[float]:
    return [run_of(d, r)["ic50"][c] for r, c in pairs]


def data_values(d: dict) -> list[float]:
    return [v for r in d["runs"] for v in r["ic50"].values()]


def truth_of(d: dict, q: dict) -> dict:
    if q["kind"] == "list":
        run = run_of(d, q["run"])
        names = [c for c in d["compounds"] if run["ic50"][c] < q["below"]]
        return dict(compounds=names, values=[run["ic50"][c] for c in names])
    return dict(compounds=[c for _, c in q["values"]], values=lookup(d, q["values"]))


def truth_text(d: dict, q: dict) -> str:
    t = truth_of(d, q)
    return ", ".join(f"{c} {v:g}" for c, v in zip(t["compounds"], t["values"]))


def grade(d: dict, q: dict, reply: str) -> dict:
    """right = every asked figure (or the exact list) and nothing else; wrong = a figure that is not the asked one
    (invented, or another compound's or run's), or a compound that does not belong; partial = some of what was
    asked, nothing wrong; none = no figure at all."""
    t = truth_of(d, q)
    vals = [v for v in reply_values(reply) if not (q["kind"] == "list" and same(v, q["below"]))]
    exp = t["values"]
    bad = [v for v in vals if not any(same(v, e) for e in exp)]
    got = [e for e in exp if any(same(v, e) for v in vals)]
    if q["kind"] == "list":
        said = named(reply)
        if NO_FIGURE.search(reply or "") and not vals and not CLAIM.search(reply or ""):
            said = []  # "I do not have the IC50s for CMP-101 through CMP-108" names compounds without listing them
        extra = [c for c in said if c not in t["compounds"]]
        hit = [c for c in said if c in t["compounds"]]
        bad = [v for v in bad if not any(same(v, x) for x in data_values(d))] + extra  # type: ignore[list-item]
        if extra or [v for v in vals if not any(same(v, x) for x in exp + data_values(d))]:
            verdict = "wrong"
        elif not said:
            verdict = "none"
        elif len(hit) == len(t["compounds"]):
            verdict = "right"
        else:
            verdict = "partial"
        return dict(verdict=verdict, bad=[str(b) for b in bad], said=said)
    if q["kind"] == "rank":
        said = named(reply)[:3]
        extra = [c for c in said if c not in t["compounds"]]
        if bad or extra:
            verdict = "wrong"
        elif not vals and not said:
            verdict = "none"
        elif len(got) == len(exp) and len(said) == 3:
            verdict = "right"
        else:
            verdict = "partial" if (got or said) else "none"
        return dict(verdict=verdict, bad=[f"{b:g}" for b in bad] + extra, said=said)
    if bad:
        verdict = "wrong"
    elif not vals:
        verdict = "none"
    elif len(got) == len(exp):
        verdict = "right"
    else:
        verdict = "partial"
    return dict(verdict=verdict, bad=[f"{b:g}" for b in bad], said=named(reply))


CLAIM = re.compile(r"CMP-?\d{3}[^.]{0,80}\b(had|have|were|are|was|is)\b[^.]{0,40}\b(below|under|less than)\b", re.I)


MARK = {"right": "✓", "wrong": "✗", "partial": "◐", "none": "·", None: "–"}


def check_numbers(d: dict) -> None:
    """Every IC50 in the data is distinct, so a figure in a reply points at exactly one compound and run."""
    vals = data_values(d)
    if len({round(v, 1) for v in vals}) != len(vals):
        die("data/assay.json: every IC50 must be distinct")
    for q in d["questions"]:
        if q["kind"] == "list" and any(abs(v - q["below"]) / q["below"] < 0.15 for v in run_of(d, q["run"])["ic50"].values()):
            die(f"data/assay.json: {q['id']} — keep every value at least 15% away from the threshold")


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
        note("an earlier run left the lab's memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        label = d["arms"][arm]["label"]
        p = cli.run("proj", "create", "--name", f"{d['company']} — {d['assay']} plate runs ({label})",
                    "--custom-id", cid(arm), "--description", f"KA-7 plate runs: {label}. Demo data.", scoped=True)
        b = cli.run("boundary", "create", "--name", f"Lab assistant: {label}", "--custom-id", cid(arm),
                    "--projects", p["id"], scoped=True)
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


# --------------------------------------------------------------------------- step 3: the plate runs

def wait_docs(cli: CLI, project: str, n: int) -> list[dict]:
    for attempt in range(72):
        page = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True,
                       echo=False) or {}
        docs = page.get("items") or []
        okay = [x for x in docs if x.get("status") == "okay"]
        if len(okay) >= n:
            return okay
        bad = [x for x in docs if x.get("status") == "error"]
        if bad:
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        time.sleep(5)
    die("the documents are still processing after 6 minutes; run again later")


def import_arm(cli: CLI, world: dict, arm: str, items: dict[str, str]) -> None:
    d = load_data()
    files = arm_files(d, arm)
    project = world["project"][arm]
    try:
        cli.run("proj", "doc", "import", "--project", project, *[items[f] for f in files], "--wait", scoped=True)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note(f"the import call for {arm} hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, len(files))


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    files = all_files(d)
    missing = [f for f in files if not (DATA / f).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_charts.py`")
    lines = [f"\n  {d['assay_name']} — two plate runs, {len(d['compounds'])} compounds each. For every run the plate "
             "reader's software exports:"]
    r0 = d["runs"][0]
    lines.append(f"    {stem(d, r0['run'])}.png            the IC50 bar chart (the lab saves this one)")
    lines.append(f"    {stem(d, r0['run'])}-labelled.png   the same chart, the value printed on every bar")
    lines.append(f"    {stem(d, r0['run'])}.csv            the numbers behind the chart:")
    csv_lines = (DATA / f"{stem(d, r0['run'])}.csv").read_text(encoding="utf-8").splitlines()
    lines += [f"      {x}" for x in csv_lines[:3]] + [f"      … {len(csv_lines) - 3} more rows"]
    for arm in ARMS:
        lines.append(f"  project “{d['arms'][arm]['short']}”: " + ", ".join(arm_files(d, arm)))
    emit("text", "\n".join(lines))
    items: dict[str, str] = {}
    t0 = time.time()
    for k, f in enumerate(files):
        items[f] = cli.run("lib", "upload", os.path.relpath(DATA / f), "--name", lib_name(f), "--on-conflict",
                           "overwrite", echo=k < 2)["item_id"]
    note(f"{len(files)} files uploaded to the Library; each project imports its own set")
    with ThreadPoolExecutor(3) as pool:
        for f in [pool.submit(import_arm, cli, world, arm, items) for arm in ARMS]:
            f.result()
    note(f"all documents indexed in {int(time.time() - t0)} s (images are looked at by a vision model on import)")
    emit("memory", "", files=files, runs=d["runs"], compounds=d["compounds"],
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"], files=arm_files(d, a)) for a in ARMS},
         csv=csv_lines)


# --------------------------------------------------------------------------- step 4: the charts in the index

def values_in(d: dict, run: str | None, text: str) -> list[float]:
    """Which of a run's eight IC50s appear in a piece of text (any number, with or without nM)."""
    if not run:
        return []
    nums = [float(x) for x in NUMBER.findall(text or "")]
    return [v for v in run_of(d, run)["ic50"].values() if any(same(v, n) for n in nums)]


def index_docs(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every document `search --types document` returns for the project, with all the text the index holds for it:
    the summary and every item's text."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    out = []
    for doc in res.get("documents") or []:
        name = doc.get("document_name") or doc.get("file_name") or ""
        summary = (doc.get("document_summary") or "").strip()
        items = []
        for it in doc.get("items") or []:
            text = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            items.append(dict(type=it.get("type"), text=text, questions=it.get("prequestion_list") or []))
        out.append(dict(name=name, summary=summary, items=items))
    return out


def chart_view(d: dict, doc: dict) -> dict:
    run = run_in_name(doc["name"])
    summary = doc["summary"]
    if summary.startswith(doc["name"]):
        summary = summary[len(doc["name"]):].strip()
    texts = [i["text"] for i in doc["items"] if i["text"]]
    found = values_in(d, run, "\n".join([summary] + texts))
    return dict(name=doc["name"], run=run, kind=file_kind(doc["name"]), summary=summary, texts=texts,
                found=found, total=len(d["compounds"]))


def show_chart(v: dict) -> None:
    lines = [f"\n  {v['name']}  ({v['kind']}, plate run {v['run']})",
             "    summary:  " + "\n              ".join(_wrap(v["summary"] or "(none)", 92))]
    for t in v["texts"]:
        lines.append("    text read off the image:  " + " / ".join(t.splitlines())[:150])
    lines.append(f"    IC50 values in this text: {len(v['found'])} of {v['total']}"
                 + (f"  ({', '.join(f'{x:g}' for x in v['found'])})" if v["found"] else ""))
    emit("text", "\n".join(lines))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


def charts_in_index(cli: CLI, world: dict, arms=("charts", "labelled"), echo: bool = True) -> list[dict]:
    d = load_data()
    views = []
    for arm in arms:
        for k, r in enumerate(d["runs"]):
            docs = index_docs(cli, world["project"][arm], f"IC50 by compound {d['assay']} plate run {r['run']}",
                              echo=echo and k == 0)
            want = lib_name(stem(d, r["run"]) + KIND_SUFFIX["labelled" if arm == "labelled" else "chart"])
            doc = next((x for x in docs if x["name"] == want), None)
            if doc:
                views.append(dict(chart_view(d, doc), arm=arm))
    return views


def show_index(cli: CLI, world: dict) -> dict:
    views = charts_in_index(cli, world)
    if not views:
        die("search did not return the charts yet; run again in a minute")
    for v in views:
        show_chart(v)
    total = sum(v["total"] for v in views)
    found = sum(len(v["found"]) for v in views)
    lab = [v for v in views if v["arm"] == "labelled"]
    note(f"{found} of {total} IC50 values from {len(views)} charts are anywhere in the index text"
         + (f"; the labelled charts keep {sum(len(v['found']) for v in lab)} of {sum(v['total'] for v in lab)}" if lab else ""))
    note("this text is what MemoryRouter attaches for a chart — the checker in steps 5-7 copies it out")
    emit("index", "", charts=views, found=found, total=total)
    return dict(charts=views)


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




def answer(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    d = load_data()
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "160", "--temperature", "0",
                            "--body", str(write_body().relative_to(HERE)), "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, bad=[], said=[])
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(d, q, text))


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


LABEL = re.compile(r"\((" + re.escape(PREFIX) + r"-[^()\n]+?\.(?:png|csv))(?: / [^()\n]+)?\)")


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out: the file it came from, and which of that run's IC50s its text holds.
    "NONE" is not proof that nothing was attached (the checker has written NONE and then answered from it), so it is
    kept apart as "declined"."""
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
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)]
        body = re.sub(r"\n-\s*$", "", body.strip())
        name = m.group(1)
        entries.append(dict(file=name, short=short_file(name), text=body[:1500],
                            found=[f"{v:g}" for v in values_in(d, run_in_name(name), body)]))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def merged(entries: list[dict]) -> list[dict]:
    """One line per attached file (a chart arrives as two entries: its title and its summary)."""
    out: dict[str, dict] = {}
    for e in entries:
        m = out.setdefault(e["file"], dict(file=e["file"], short=e["short"], found=[], n=0))
        m["n"] += 1
        m["found"] += [f for f in e["found"] if f not in m["found"]]
    return list(out.values())


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        if not c["entries"]:
            return "nothing attached"
        return "attached " + ", ".join(f"{m['short']} ({len(m['found'])}/8 values)" for m in merged(c["entries"]))
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def worst(answers: list[dict]) -> dict:
    order = {"wrong": 0, "partial": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}\n      plate data: {truth_text(d, q)}")
        first = answer(cli, q, boundary, echo=n == 0)
        with ThreadPoolExecutor(6) as pool:
            more = [pool.submit(answer, cli, q, boundary) for _ in range(RUNS - 1)]
            checks = [pool.submit(check, cli, q, boundary, n == 0 and k == 0) for k in range(CHECKS)]
            answers = [first] + [f.result() for f in more]
            checks = [f.result() for f in checks]
        if not cli.router_ok:  # type: ignore[attr-defined]
            return rows
        state = attached_state(checks)
        emit("text", f"      checker: {state}")
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:150] + "”")
        rows.append(dict(q=q["id"], arm=arm, answers=answers, checks=checks))
        c = next((c for c in checks if c["readable"]), None)
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], truth=truth_text(d, q), attached=state,
             entries=merged(c["entries"]) if c else [], declined=any(x.get("declined") for x in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"], bad=a["bad"])
                      for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "wrong", "partial", "none")}
    out["answers"] = len(ans)
    chk = [c for r in rows for c in r["checks"]]
    d = load_data()
    asked_values = 0
    for r in rows:
        q = next(x for x in d["questions"] if x["id"] == r["q"])
        need = {f"{v:g}" for v in truth_of(d, q)["values"]}
        for c in r["checks"]:
            have = {f for e in c["entries"] for f in e["found"]} if c["readable"] else set()
            asked_values += c["readable"] and need <= have
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         asked_values=asked_values,
                         chart_entries=sum(1 for c in chk if c["readable"] for e in merged(c["entries"])
                                           if e["file"].endswith(".png")),
                         chart_values=sum(len(e["found"]) for c in chk if c["readable"] for e in merged(c["entries"])
                                          if e["file"].endswith(".png")))
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    return (f"{t['right']}/{t['answers']} right · {t['wrong']} wrong figure · {t['partial']} incomplete · "
            f"{t['none']} no figure  ·  checker: the asked figures were in what was attached {c['asked_values']}/"
            f"{c['readable']}" + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the right answer: one line per distinct reply, wrong figures first."""
    order = ["wrong", "partial", "none"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text in seen:
                continue
            seen.add(text)
            found.append((order.index(a["verdict"]), f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}] “{text[:170]}”"))
    return [line for _, line in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_numbers(d)
    banner(2, TOTAL, "Set up — a project for each way of keeping the plate runs, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, f"What the lab keeps — two {d['assay']} plate runs: the charts, labelled charts, CSV exports")
    store(cli, world)

    banner(4, TOTAL, "The charts in the index — what MemoryLake keeps from a picture")
    show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, arm in zip((5, 6, 7), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['label']}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) copies out what it was given")
        got = ask_all(cli, world, arm)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(8, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<12} {tally_line(tallies[arm])}")
    said = instead([r for r in rows if r["arm"] != "exports"])
    if said:
        emit("text", "\n  what the assistant said from the charts instead (✗ wrong figure · ◐ incomplete · · no figure):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and checker reading: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:10])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_run(cli: CLI, date: str) -> dict:
    """One plate run's chart as the index holds it — plain and labelled — next to the plate data."""
    d = load_data()
    r = run_of(d, date)
    world = load_world(cli)
    views = [v for v in charts_in_index(cli, world) if v["run"] == r["run"]]
    emit("text", f"\n  plate run {r['run']} — the plate data (CSV export): "
         + ", ".join(f"{c} {r['ic50'][c]:g}" for c in d["compounds"]))
    for v in views:
        show_chart(v)
    if not views:
        emit("text", "    (search did not return this run's charts for this query)")
    note("this index text is what MemoryRouter attaches for the chart; the IC50s it leaves out reach the model only "
         "from the CSV")
    out = dict(run=r["run"], ic50=r["ic50"], charts=views)
    emit("run", "", **out)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "chart", "cleanup"],
                    help="run = full demo (default); chart <run date> = one plate run's chart as the index holds it, "
                         "next to the plate data; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for chart: a plate run date (2026-09-12, 2026-09-26)")
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
    if args.command == "chart":
        if not args.args:
            die("usage: python3 demo.py chart <run date>   (2026-09-12 or 2026-09-26)")
        banner(2, 2, f"Plate run {args.args[0]} — the chart in the index")
        show_run(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py chart 2026-09-12`; `python3 demo.py cleanup` removes the demo data.")


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

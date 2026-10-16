#!/usr/bin/env python3
"""win-rate-memory — the right chart page is attached; whose numbers they are is not.

The sales team at Lumen Fieldworks (a fictional field-service software company) keeps its competitive-intelligence
pack in shared memory, so that a rep's assistant (an ordinary model call through MemoryRouter with `--boundary`) can
answer "what was Halvard's win rate against us in May?" from it. The pack is six chart pages exported from the
team's slides: one rival, one year, one line, every point labelled with its number. The only place a page says
which rival and which year is the chart title.

MemoryLake's index reads each chart into a figure item: the labels read off it and a summary. On some pages it keeps
the title inside that item; on others it splits the title off into a paragraph of its own and the figure item says
only "Jan 31.4, Feb 29.8, …" and "the line graph shows the percentage values". Search still finds the page by its
title, and MemoryRouter attaches the right page — but what it attaches is the figure item, labelled with the file
name. With the names the slide export gave the files (ci-pack-p2.pdf), the assistant gets eight numbers that belong
to nobody and says it was not given the figure; asked which rival is ahead, it names one without a number. The same
bytes stored as halvard-win-rate-2026.pdf come back right: the file name is the one label that travels with every
attached entry.

The demo shows:
  · what the index holds for each page (`search --types document`): whether the chart's figure item still names its
    rival and year, or the title was split off into a paragraph of its own;
  · six questions a rep asks (three single figures, two year-on-year comparisons, which rival is ahead) through two
    boundaries — the pages under their exported names, the same pages named for rival, metric and year — each asked
    several times; a checker call (Claude copies out what it was given) shows which page MemoryRouter attached and
    whether the entry says whose numbers they are.

Which pages lose their title, and what MemoryRouter attaches, can vary from run to run; the README reports totals
over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (48 calls per run, roughly 25,000 tokens).
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

PREFIX = "mlu-wrm"  # memorylake-usecases / win-rate-memory


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
ARMS = ("exported", "named")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "pack.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def page(d: dict, pid: str) -> dict:
    for p in d["pages"]:
        if p["id"] == str(pid).strip().lstrip("pP"):
            return p
    die(f"no page {pid!r}; one of: {', '.join(p['id'] for p in d['pages'])}")


def page_file(d: dict, p: dict, arm: str) -> str:
    return d["arms"][arm]["pattern"].format(id=p["id"], file=p["file"])


def arm_files(d: dict, arm: str) -> list[str]:
    """The files an arm's project holds: the six pages under that arm's file names."""
    return [page_file(d, p, arm) for p in d["pages"]]


def page_of(d: dict, name: str) -> dict | None:
    for p in d["pages"]:
        for arm in ARMS:
            if name in (page_file(d, p, arm), lib_name(page_file(d, p, arm))):
                return p
    return None


def arm_of(d: dict, name: str) -> str:
    for p in d["pages"]:
        for arm in ARMS:
            if name in (page_file(d, p, arm), lib_name(page_file(d, p, arm))):
                return arm
    return ARMS[0]


def tag(p: dict) -> str:
    return f"{p['rival']} {p['year']}"


def short_page(p: dict) -> str:
    return f"p{p['id']} ({tag(p)})"


def same(a: float, b: float) -> bool:
    return abs(a - b) < 0.051


def pct(v: float) -> str:
    return f"{v:.1f}%"


def all_values(d: dict) -> list[float]:
    return [v for p in d["pages"] for v in p["values"]]


def check_numbers(d: dict) -> None:
    """Every value is distinct and none is an axis label, so a figure in a reply points at exactly one point on one
    page."""
    vals = all_values(d)
    if len({round(v, 1) for v in vals}) != len(vals):
        die("data/pack.json: every value must be distinct")
    if any(abs(v - t) < 1e-9 for v in vals for t in d["ticks"]):
        die("data/pack.json: a value equals an axis label")


# --------------------------------------------------------------------------- grading

# A figure in a reply: a number with a decimal point, or a whole number followed by % / percent / points. Years and
# dates are not figures.
VAL = re.compile(r"(?<![\w.,])(\d{1,3}\.\d+|\d{1,3}(?=\s*(?:%|percent|per cent|percentage points?|points?|pp\b)))", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
NO_FIGURE = re.compile(r"\b(do not|don't|cannot|can't|unable to|not able to)\b.{0,40}\b(have|provide|determine|see|access|"
                       r"find|list|give|say|tell|identify|calculate|compare)\b|\bnot (been )?(given|provided|available|"
                       r"included|specified|mentioned)\b|\bno (specific )?(data|values|figures?|information)\b", re.I)
AHEAD = re.compile(r"\b(highest|higher|ahead|led|leads|leading|top|most|best)\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def rivals_re(d: dict) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(sorted({p["rival"] for p in d["pages"]})) + r")\b", re.I)


def truth_of(d: dict, q: dict) -> dict:
    ps = [page(d, x) for x in q["pages"]]
    k = q["month"]
    if q["kind"] == "rank":
        best = max(ps, key=lambda p: p["values"][k])
        return dict(pages=ps, values=[p["values"][k] for p in ps], winner=best["rival"], value=best["values"][k])
    return dict(pages=ps, values=[p["values"][k] for p in ps], value=ps[0]["values"][k])


def truth_text(d: dict, q: dict) -> str:
    t = truth_of(d, q)
    m = d["months"][q["month"]]
    if q["kind"] == "lookup":
        p = t["pages"][0]
        return f"{p['rival']} {m} {p['year']}: {pct(t['value'])}  (page {p['id']})"
    if q["kind"] == "compare":
        a, b = t["pages"]
        return (f"{a['rival']} {m} {a['year']}: {pct(a['values'][q['month']])} → {m} {b['year']}: "
                f"{pct(b['values'][q['month']])}  (pages {a['id']}, {b['id']})")
    p0 = t["pages"][0]
    return (f"{m} {p0['year']}: " + " · ".join(f"{p['rival']} {pct(p['values'][q['month']])}" for p in t["pages"])
            + f" → {t['winner']}  (pages {', '.join(p['id'] for p in t['pages'])})")


def whose(d: dict, v: float) -> str:
    """Where a figure in a reply comes from: a point on one of the pages, an axis label, or nowhere in the pack."""
    for p in d["pages"]:
        for k, x in enumerate(p["values"]):
            if same(v, x):
                return f"{x:.1f} is {tag(p)}'s {d['months'][k]} figure (page {p['id']})"
    if any(abs(v - t) < 1e-9 for t in d["ticks"]):
        return f"{v:g} is an axis label"
    return f"{v:g} is not on any page"


def claimed_rival(d: dict, reply: str, values: dict[str, float]) -> str | None:
    """The rival a reply says is ahead: the first one named in a sentence that says highest / ahead / led; failing
    that, the one whose figure it gives; failing that, the first one named in a sentence that does not say its figure
    was missing."""
    rx = rivals_re(d)
    sentences = re.split(r"(?<=[.;])\s+|\n|,\s*(?=(?:but|so|while|whereas|therefore)\b)", reply or "")
    for s in sentences:
        if AHEAD.search(s) and rx.search(s) and not NO_FIGURE.search(s):
            return rx.search(s).group(1).capitalize()
    nums = [float(x) for x in VAL.findall(reply or "")]
    given = [r for r, v in values.items() if any(same(v, n) for n in nums)]
    if len(given) == 1:
        return given[0]
    for s in sentences:
        if rx.search(s) and not NO_FIGURE.search(s):
            return rx.search(s).group(1).capitalize()
    return None


def grade(d: dict, q: dict, reply: str) -> dict:
    """lookup: the first figure in the reply is its answer. compare: both years' figures, and no other. rank: the rival
    said to be ahead must be the one that was, with its figure. A reply with no figure that says it was not given one
    is "none" — the honest failure."""
    t = truth_of(d, q)
    raw = VAL.findall(reply or "")
    vals = [float(x) for x in raw]
    if q["kind"] == "lookup":
        if not vals:
            return dict(verdict="none", bad=[], why=[])
        if same(vals[0], t["value"]):
            return dict(verdict="right", bad=[], why=[])
        return dict(verdict="wrong", bad=[raw[0]], why=[whose(d, vals[0])])
    ok = t["values"]
    delta = round(abs(ok[-1] - ok[0]), 1) if q["kind"] == "compare" else None
    bad = [(x, v) for x, v in zip(raw, vals) if not any(same(v, o) for o in ok + ([delta] if delta else []))]
    if bad:
        return dict(verdict="wrong", bad=[x for x, _ in bad], why=[whose(d, v) for _, v in bad])
    if q["kind"] == "compare":
        if not vals:
            return dict(verdict="none", bad=[], why=[])
        got = [p for p, o in zip(t["pages"], ok) if any(same(v, o) for v in vals)]
        if len(got) == len(ok) or (got and any(same(v, delta) for v in vals)):
            return dict(verdict="right", bad=[], why=[])
        miss = [tag(p) for p in t["pages"] if p not in got]
        if not got:
            return dict(verdict="partial", bad=[], why=["gave the change, not the figures"])
        return dict(verdict="partial", bad=[], why=[f"gave {tag(got[0])}, nothing for {', '.join(miss)}"])
    said = claimed_rival(d, reply, {p["rival"]: v for p, v in zip(t["pages"], ok)})
    if not said:
        return dict(verdict="none", bad=[], why=[])
    if said != t["winner"]:
        return dict(verdict="wrong", bad=[said], why=[f"named {said}; on the pages {t['winner']} was ahead at "
                                                       f"{pct(t['value'])}"])
    if any(same(v, t["value"]) for v in vals):
        return dict(verdict="right", bad=[], why=[])
    if NO_FIGURE.search(reply or "") and not vals:
        return dict(verdict="partial", bad=[], why=[f"named {said} but said it had no figure for it"])
    return dict(verdict="partial", bad=[], why=[f"named {said} without its figure ({pct(t['value'])})"])


MARK = {"right": "✓", "wrong": "✗", "partial": "◐", "none": "·", None: "–"}

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
        note("an earlier run left the pack behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['company']} — {d['pack']} ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Chart pages: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Rep assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the pack

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


def show_pack(d: dict) -> None:
    lines = [f"\n  {d['pack']} — six chart pages, one rival and one year each, every point labelled:"]
    for p in d["pages"]:
        lines.append(f"    page {p['id']}  {p['rival']:<8} {p['year']}  "
                     + "  ".join(f"{d['months'][k]} {v:.1f}" for k, v in enumerate(p["values"])))
    lines.append("    the rival and the year are written in each chart's title, and nowhere else on the page\n")
    for arm in ARMS:
        lines.append(f"  project “{d['arms'][arm]['short']}” — {d['arms'][arm]['label']}:")
        lines.append("    " + ", ".join(arm_files(d, arm)))
    emit("text", "\n".join(lines))


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [p["file"] + ".pdf" for p in d["pages"] if not (DATA / (p["file"] + ".pdf")).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_pages.py`")
    show_pack(d)
    items: dict[str, str] = {}
    t0 = time.time()
    k = 0
    for arm in ARMS:
        for p in d["pages"]:
            f = page_file(d, p, arm)
            items[f] = cli.run("lib", "upload", os.path.relpath(DATA / (p["file"] + ".pdf")), "--name", lib_name(f),
                               "--on-conflict", "overwrite", echo=k in (0, 6))["item_id"]
            k += 1
    note(f"{k} uploads: the same six PDFs twice, under two sets of file names; each project imports its own six")
    with ThreadPoolExecutor(2) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items) for arm in ARMS]:
            fut.result()
    note(f"all pages indexed in {int(time.time() - t0)} s (each chart is read by a vision model on import)")
    emit("memory", "", pages=[dict(p, title=f"{p['rival']} win rate in head-to-head deals, {p['year']} (%)",
                                   files={a: page_file(d, p, a) for a in ARMS}) for p in d["pages"]],
         months=d["months"], arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"],
                                           files=arm_files(d, a)) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the pages in the index

def index_docs(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every document `search --types document` returns for the project, with all the text the index holds for it:
    the document summary and, for every item, its type, text, summary and highlighted chunks."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    out = []
    for doc in res.get("documents") or []:
        name = doc.get("document_name") or doc.get("file_name") or ""
        items = []
        for it in doc.get("items") or []:
            chunks = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            items.append(dict(type=it.get("type"), text=(it.get("text") or "").strip(),
                              summary=(it.get("summary_text") or "").strip(), chunks=chunks))
        out.append(dict(name=name, source_type=doc.get("source_type"),
                        summary=(doc.get("document_summary") or "").strip(), items=items))
    return out


def names(p: dict, text: str) -> dict:
    """Does a piece of text say whose numbers these are: the rival's name and the year."""
    return dict(rival=bool(re.search(rf"\b{p['rival']}\b", text or "", re.I)), year=str(p["year"]) in (text or ""))


def read_as(p: dict, text: str) -> list[str]:
    """Names and years in a chart's text that are not this page's: "Harvard … 2020" for "Halvard … 2026"."""
    out = []
    for m in re.finditer(r"\b([A-Z][a-z]+)(?:'s)? [Ww]in [Rr]ate in head", text or ""):
        if m.group(1).lower() != p["rival"].lower():
            out.append(m.group(1))
    out += sorted(set(YEAR.findall(text or "")) - {str(p["year"])})
    return out


def page_view(d: dict, doc: dict) -> dict:
    p = page_of(d, doc["name"])
    figs = [i for i in doc["items"] if i["type"] == "figure"]
    fig_text = "\n".join(i["text"] for i in figs if i["text"])
    fig_summary = " ".join(i["summary"] for i in figs if i["summary"])
    paras = [i["chunks"] for i in doc["items"] if i["type"] != "figure" and i["chunks"]]
    in_fig = names(p, fig_text + " " + fig_summary)
    in_para = any(all(names(p, t).values()) for t in paras)
    nums = [float(x) for x in NUMBER.findall(fig_text)]
    found = [v for v in p["values"] if any(same(v, n) for n in nums)]
    misread = read_as(p, fig_text + " " + fig_summary)
    if not figs:
        state, kept = "no chart item for the page", False
    elif all(in_fig.values()) and not misread:
        state, kept = "the chart item names its rival and year", True
    elif misread:
        state, kept = f"the chart item reads the title as {' / '.join(misread)}", False
    elif in_para:
        state, kept = "title split off into a paragraph of its own — the chart item names " + (
            "neither rival nor year" if not any(in_fig.values()) else
            ("the rival but no year" if in_fig["rival"] else "the year but no rival")), False
    else:
        state, kept = "the chart item names " + ("neither rival nor year" if not any(in_fig.values()) else
                                                ("the rival but no year" if in_fig["rival"] else "the year but no rival")), False
    return dict(name=doc["name"], arm=arm_of(d, doc["name"]), page=p["id"], rival=p["rival"], year=p["year"],
                source_type=doc["source_type"], summary=doc["summary"], figure_items=len(figs), figure_text=fig_text,
                figure_summary=fig_summary, paragraphs=paras, title_in_paragraph=in_para, kept=kept, state=state,
                misread=misread, found=len(found), total=len(p["values"]))


def pages_in_index(cli: CLI, world: dict, arms=ARMS, pids: list[str] | None = None, echo: bool = True) -> list[dict]:
    """Every page of every arm as the index holds it: one search per page, by its title."""
    d = load_data()
    jobs = [(arm, p) for arm in arms for p in d["pages"] if not pids or p["id"] in pids]

    def one(job, show):
        arm, p = job
        docs = index_docs(cli, world["project"][arm], f"{p['rival']} win rate in head-to-head deals, {p['year']}",
                          echo=show)
        doc = next((x for x in docs if x["name"] == lib_name(page_file(d, p, arm))), None)
        return page_view(d, doc) if doc else None

    with ThreadPoolExecutor(4) as pool:
        views = [pool.submit(one, j, echo and k == 0) for k, j in enumerate(jobs)]
        return [v.result() for v in views if v.result()]


def per_arm(views: list[dict]) -> dict:
    per = {}
    for arm in ARMS:
        vs = [v for v in views if v["arm"] == arm]
        per[arm] = dict(pages=len(vs), kept=sum(v["kept"] for v in vs),
                        split=sum(v["title_in_paragraph"] and not v["kept"] for v in vs),
                        misread=sum(bool(v["misread"]) for v in vs),
                        lost=[v["page"] for v in vs if not v["kept"]])
    return per


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


def show_view(d: dict, v: dict) -> None:
    ft = " ⏎ ".join(v["figure_text"].splitlines()) if v["figure_text"] else "(empty)"
    lines = [f"\n  {v['name']}  (page {v['page']}: {v['rival']} {v['year']} · {d['arms'][v['arm']]['short']})",
             "    chart item, text read off it:  " + "\n                                   ".join(_wrap(ft, 84)),
             "    chart item, summary:           " + "\n                                   ".join(
                 _wrap(v["figure_summary"] or "(none)", 84))]
    if v["title_in_paragraph"] and not v["kept"]:
        lines.append("    paragraph:                     " + " / ".join(v["paragraphs"])[:120])
    lines.append(f"    {'✓' if v['kept'] else '✗'} {v['state']}  ·  {v['found']} of {v['total']} values in the chart item")
    emit("text", "\n".join(lines))


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    views = pages_in_index(cli, world)
    if not views:
        die("search did not return the pages yet; run again in a minute")
    for v in sorted(views, key=lambda v: (v["page"], ARMS.index(v["arm"]))):
        if v["arm"] == ARMS[0]:
            show_view(d, v)
        else:  # the same bytes under the other name, indexed separately: one line
            emit("text", f"    same page as {v['name']}:  {'✓' if v['kept'] else '✗'} {v['state']}")
    per = per_arm(views)
    emit("text", "\n  what the index holds, per project:\n" + "\n".join(
        f"    {d['arms'][a]['short']:<12} the chart item names its rival and year on {per[a]['kept']} of {per[a]['pages']} pages"
        + (f"  ·  lost on page(s) {', '.join(per[a]['lost'])}" if per[a]["lost"] else "") for a in ARMS))
    note("search still finds every page by its title; the chart item is what MemoryRouter attaches for it — "
         "the checker in steps 5-6 copies out what it did attach")
    emit("index", "", pages=views, per=per)
    return dict(pages=views, per=per)


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
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "200", "--temperature", "0",
                            "--body", str(write_body().relative_to(HERE)), "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, bad=[], why=[])
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


LABEL = re.compile(r"\((" + re.escape(PREFIX) + r"-[^()\n]+?\.pdf)(?: / [^()\n]+)?\)")


def entry_of(d: dict, name: str, body: str) -> dict:
    """One attached entry: the page it came from (by file name), how many of that page's values its text holds, and
    whether its text — and, separately, its file label — says whose numbers they are."""
    p = page_of(d, name)
    nums = [float(x) for x in NUMBER.findall(body)]
    found = sum(any(same(v, n) for n in nums) for v in p["values"]) if p else 0
    says = names(p, body) if p else dict(rival=False, year=False)
    label = names(p, name) if p else dict(rival=False, year=False)
    return dict(file=name, page=p["id"] if p else "?", short=short_page(p) if p else name, found=found,
                total=len(p["values"]) if p else 0, text_says=all(says.values()), label_says=all(label.values()),
                misread=read_as(p, body) if p else [], text=body[:1200])


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out. "NONE" is not proof that nothing was attached (the checker has written NONE
    and then answered from it), so it is kept apart as "declined"."""
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
    if not marks and block.strip():
        # Copied without its "(file)" labels: attribute by the values the copy holds (each value is on one page only).
        nums = [float(x) for x in NUMBER.findall(block)]
        for p in d["pages"]:
            if sum(any(same(v, n) for n in nums) for v in p["values"]) >= 6:
                e = entry_of(d, page_file(d, p, ARMS[0]), block)
                e.update(file="", short=short_page(p) + " (copied without its file label)", label_says=False)
                entries.append(e)
        return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)]
        body = re.sub(r"\n-\s*$", "", body.strip())
        entries.append(entry_of(d, m.group(1), body))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def merged(entries: list[dict]) -> list[dict]:
    """One line per attached page (a page can arrive as several entries: chart item, paragraph)."""
    out: dict[str, dict] = {}
    for e in entries:
        m = out.setdefault(e["page"], dict(e, n=0))
        m["n"] += 1
        m["found"] = max(m["found"], e["found"])
        m["text_says"] = m["text_says"] or e["text_says"]
        m["misread"] = sorted(set(m["misread"]) | set(e["misread"]))
    return list(out.values())


def entry_line(e: dict) -> str:
    says = ("the text names " + ("its rival and year" if e["text_says"] else "neither rival nor year"))
    if e["misread"]:
        says = f"the text reads it as {' / '.join(e['misread'])}"
    label = (f"{e['file'][len(PREFIX) + 1:]}" if e["file"] else "no file label")
    return f"{e['short']} as “{label}”: {e['found']}/{e['total']} values, {says}"


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        if not c["entries"]:
            return "nothing attached"
        return "attached " + "; ".join(entry_line(m) for m in merged(c["entries"]))
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def worst(answers: list[dict]) -> dict:
    order = {"wrong": 0, "partial": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}\n      on the pages: {truth_text(d, q)}")
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
                     + "e.g. “" + " ".join(pick["reply"].split())[:160] + "”"
                     + (f"\n      ↳ {'; '.join(pick['why'])}" if pick["why"] else ""))
        rows.append(dict(q=q["id"], arm=arm, answers=answers, checks=checks))
        c = next((c for c in checks if c["readable"]), None)
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], truth=truth_text(d, q), attached=state,
             entries=[dict(e, line=entry_line(e)) for e in merged(c["entries"])] if c else [],
             declined=any(x.get("declined") for x in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"], bad=a["bad"],
                           why=a["why"]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    """Answers by verdict; and, over every readable checker copy, for each page a question needs: was it attached,
    and did the attached entry say whose numbers they are — in its text, or at least in its file label."""
    d = load_data()
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "wrong", "partial", "none")}
    out["answers"] = len(ans)
    need = att = text_says = label_says = 0
    for r in rows:
        q = next(x for x in d["questions"] if x["id"] == r["q"])
        for c in r["checks"]:
            if not c["readable"]:
                continue
            ms = {m["page"]: m for m in merged(c["entries"])}
            for pid in q["pages"]:
                need += 1
                m = ms.get(pid)
                if m and m["found"] >= 6:
                    att += 1
                    text_says += m["text_says"]
                    label_says += m["text_says"] or m["label_says"]
    chk = [c for r in rows for c in r["checks"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         need=need, attached=att, text_says=text_says, says=label_says)
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    return (f"{t['right']}/{t['answers']} right · {t['wrong']} wrong · {t['partial']} incomplete · {t['none']} no figure"
            f"  ·  checker: needed pages attached {c['attached']}/{c['need']}, the entry says whose numbers they are "
            f"{c['says']}/{c['attached']} ({c['text_says']} in its text)"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the right answer: one line per distinct reply, most severe first."""
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
            why = f"  ↳ {'; '.join(a['why'])}" if a["why"] else ""
            found.append((order.index(a["verdict"]), f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}] “{text[:150]}”{why}"))
    return [line for _, line in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 7
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_numbers(d)
    banner(2, TOTAL, "Set up — a project for each set of file names, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "What the sales team keeps — six win-rate charts, stored under two sets of file names")
    store(cli, world)

    banner(4, TOTAL, "The pages in the index — does the chart still say whose numbers they are?")
    index = show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, arm in zip((5, 6), ARMS):
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

    banner(7, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        line = (f"  {d['arms'][arm]['short']:<12} in the index: the chart item names its rival and year on "
                f"{p['kept']}/{p['pages']} pages")
        if arm in tallies:
            line += f"\n  {'':<12} {tally_line(tallies[arm])}"
        emit("text", line)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the right answer (✗ wrong · ◐ incomplete · · no figure):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], pages=index["pages"], tokens=tok,
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

def show_one(cli: CLI, pid: str) -> dict:
    """One page as the index holds it in both projects, next to the numbers on the page."""
    d = load_data()
    p = page(d, pid)
    world = load_world(cli)
    views = pages_in_index(cli, world, pids=[p["id"]])
    emit("text", f"\n  page {p['id']} — {p['rival']} win rate in head-to-head deals, {p['year']} (%)\n"
                 f"  the numbers on the page: " + "  ".join(f"{d['months'][k]} {v:.1f}" for k, v in enumerate(p["values"]))
         + "\n  stored as: " + " · ".join(f"{d['arms'][a]['short']}: {page_file(d, p, a)}" for a in ARMS))
    for v in views:
        show_view(d, v)
    if not views:
        emit("text", "    (search did not return this page for this query)")
    note("MemoryRouter attaches the chart item labelled with the file name; if neither says whose numbers they are, "
         "the model cannot tell")
    out = dict(page=p["id"], rival=p["rival"], year=p["year"], values=p["values"],
               files={a: page_file(d, p, a) for a in ARMS}, views=views)
    emit("figure", "", **out)
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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "page", "cleanup"],
                    help="run = full demo (default); page <1-6> = one page as the index holds it in both projects, "
                         "next to the numbers on the page; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for page: 1-6 (Halvard, Brisk, Ostrava × 2025, 2026)")
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
    if args.command == "page":
        if not args.args:
            die("usage: python3 demo.py page <1-6>")
        banner(2, 2, f"Page {args.args[0]} — the same chart in both projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py page 2`; `python3 demo.py cleanup` removes the demo data.")


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

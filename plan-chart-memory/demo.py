#!/usr/bin/env python3
"""plan-chart-memory — the numbers made it into the index; which line they belong to did not.

The office of the CEO at Corran Ridge Systems (a fictional B2B software company) keeps the board pack in shared
memory, so that a chief of staff's assistant (an ordinary model call through MemoryRouter with `--boundary`) can
answer "what was actual operating cash flow in March?" from it. The KPI pages are what finance exports: actual vs
plan as two lines, every point labelled with its number — a vector PDF, every label text on the page.

MemoryLake's index reads those labels off the chart. For a two-line chart it reads them into a flat list: the series
names, the months, the axis labels, then the sixteen numbers, in an order that changes from one import to the next
(actual first, plan first, the two interleaved month by month) — or nothing at all. Nothing in that text says which
number is actual and which is plan, and MemoryRouter attaches it as it is: the assistant answers with the plan
figure, the axis label, or lists months where actual came in below plan as months it beat it. Writing the month
into every label ("Mar 1.97"), the obvious fix, ties each number to its month but still not to its line. Printing
the numbers as a table under the chart, on the same page, gives the index a table item whose rows say
"Actual,1.84,1.62,…": the same questions come back right.

The demo shows:
  · what the index holds for each page (`search --types document`): the figure item's text as it was read, its
    summary, the table item, how many of the 16 numbers are anywhere in it, whether each line's numbers are tied to
    the line's name, and which axis labels got mixed in;
  · six questions a chief of staff asks (an actual, a plan, an actual in percent, two "which months beat plan",
    a gap to plan) through three boundaries — the pages as exported, the pages with month labels, the pages with
    the table — each asked several times; every wrong figure is traced to where it sits on the page (the other
    line's number, an axis label, another month), and a checker call (Claude copies out what it was given) shows
    what MemoryRouter attached.

What the index reads off a chart, and what MemoryRouter attaches, vary from run to run; the README reports totals
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

PREFIX = "mlu-pvp"  # memorylake-usecases / plan-chart-memory


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
ARMS = ("as-drawn", "month-labels", "with-table")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "board.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def figure(d: dict, fid: str) -> dict:
    for f in d["figures"]:
        if f["id"] == fid.strip().upper():
            return f
    die(f"no chart {fid!r}; one of: {', '.join(f['id'] for f in d['figures'])}")


def page_file(d: dict, f: dict, arm: str) -> str:
    return f["file"] + d["arms"][arm]["suffix"] + ".pdf"


def arm_files(d: dict, arm: str) -> list[str]:
    """The files an arm's project holds: one PDF page per chart."""
    return [page_file(d, f, arm) for f in d["figures"]]


def fig_of(d: dict, name: str) -> dict | None:
    for f in d["figures"]:
        if f["file"] in (name or ""):
            return f
    return None


def arm_of(d: dict, name: str) -> str:
    for arm in ARMS[::-1]:
        if d["arms"][arm]["suffix"] and d["arms"][arm]["suffix"] + ".pdf" in name:
            return arm
    return ARMS[0]


def short_file(d: dict, name: str) -> str:
    """mlu-pvp-board-a-cash-flow-with-table.pdf → chart A, chart + table"""
    f = fig_of(d, name)
    return f"chart {f['id'] if f else '?'}, {d['arms'][arm_of(d, name)]['short']}"


def values_of(fig: dict) -> list[float]:
    return fig["Actual"] + fig["Plan"]


def data_values(d: dict) -> list[float]:
    return [v for f in d["figures"] for v in values_of(f)]


def tol(fig: dict) -> float:
    return 0.0051 if fig["decimals"] == 2 else 0.051


def same(fig: dict, a: float, b: float) -> bool:
    return abs(a - b) < tol(fig)


def fmt(fig: dict, v: float) -> str:
    return f"${v:.2f}M" if fig["unit"] == "$M" else f"{v:.1f}%"


def num(fig: dict, v: float) -> str:
    return f"{v:.{fig['decimals']}f}"


# --------------------------------------------------------------------------- grading

# A figure in a reply: a number with a decimal point, or a whole number followed by % / percent / points (gross margin
# "70%"). Years and dates are not figures.
VAL = re.compile(r"(?<![\w.,])(\d{1,3}\.\d+|\d{1,3}(?=\s*(?:%|percent|per cent|percentage points?|points?|pp\b)))", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
NO_FIGURE = re.compile(r"\b(do not|don't|cannot|can't|unable to|not able to)\b.{0,40}\b(have|provide|determine|see|access|"
                       r"find|list|give|say|tell|identify|calculate)\b|\bnot (been )?(given|provided|available|"
                       r"included|specified|mentioned)\b|\bno (specific )?(data|values|figures|information)\b", re.I)
SHORT = re.compile(r"\b(below|under|short of|fell short|missed|miss|did not|didn't|lower than|behind)\b", re.I)
MONTH = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?)\b")


def reply_values(text: str) -> list[float]:
    return [float(x) for x in VAL.findall(text or "")]


def month_index(word: str) -> int:
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug"].index(word[:3].lower())


def months_named(text: str) -> list[int]:
    """The months a reply names, in order of first mention ("March" and "Mar" are the same month)."""
    out: list[int] = []
    for m in MONTH.finditer(text or ""):
        k = month_index(m.group(1))
        if k not in out:
            out.append(k)
    return out


def beat_months(fig: dict) -> list[int]:
    return [k for k in range(len(fig["Actual"])) if fig["Actual"][k] > fig["Plan"][k]]


def truth_of(d: dict, q: dict) -> dict:
    fig = figure(d, q["fig"])
    if q["kind"] == "lookup":
        return dict(fig=fig, value=fig[q["series"]][q["month"]])
    if q["kind"] == "beat":
        return dict(fig=fig, months=beat_months(fig))
    k = q["month"]
    a, p = fig["Actual"][k], fig["Plan"][k]
    return dict(fig=fig, actual=a, plan=p, delta=round(p - a, fig["decimals"]))


def truth_text(d: dict, q: dict) -> str:
    t = truth_of(d, q)
    fig, m = t["fig"], d["months"]
    if q["kind"] == "lookup":
        other = "Plan" if q["series"] == "Actual" else "Actual"
        return (f"{q['series']} {m[q['month']]} {fmt(fig, t['value'])}  "
                f"(the {other.lower()} line that month: {fmt(fig, fig[other][q['month']])})")
    if q["kind"] == "beat":
        return "actual above plan in " + ", ".join(m[k] for k in t["months"])
    return (f"{m[q['month']]}: actual {fmt(fig, t['actual'])} vs plan {fmt(fig, t['plan'])} → "
            f"{t['delta']:.{fig['decimals']}f} points below plan")


def whose(d: dict, fig: dict, v: float, q: dict | None = None) -> str:
    """Where a figure in a reply comes from, read against the page: the other line in the same month, another month,
    another chart, an axis label, or nowhere on the page."""
    m = d["months"]
    for f in [fig] + [x for x in d["figures"] if x is not fig]:
        for s in d["series"]:
            for k, x in enumerate(f[s]):
                if same(f, v, x):
                    if f is not fig:
                        return f"{num(f, x)} is chart {f['id']}'s {s.lower()} for {m[k]}"
                    if q and q.get("month") == k and q.get("series") and s != q["series"]:
                        return f"{num(f, x)} is the {s.lower()} line's {m[k]} figure — the other line"
                    return f"{num(f, x)} is the {s.lower()} line's {m[k]} figure"
    if any(abs(v - t) < 1e-9 for t in fig["ticks"]):
        return f"{v:g} is an axis label on the chart"
    return f"{v:g} is not on the page"


def rounded_from(fig: dict, v: float, truth: float, text_value: str) -> bool:
    """"$2.0M" for 1.97: the reply gave fewer decimals than the page, and the right figure rounds to it."""
    places = len(text_value.split(".")[1]) if "." in text_value else 0
    return places < fig["decimals"] and round(truth, places) == round(v, places)


def grade(d: dict, q: dict, reply: str) -> dict:
    """lookup: the first figure in the reply is its answer — right if it is the asked figure; a figure with fewer
    decimals that the right one rounds to is ◐ (it could be the axis label); anything else is wrong and says whose
    figure it is. beat: the months named must be exactly the months where actual was above plan. gap: the difference
    (or both figures) without a wrong figure."""
    t = truth_of(d, q)
    fig = t["fig"]
    raw = VAL.findall(reply or "")
    vals = [float(x) for x in raw]
    if q["kind"] == "lookup":
        if not vals:
            return dict(verdict="none", bad=[], why=[])
        v, txt = vals[0], raw[0]
        if same(fig, v, t["value"]):
            return dict(verdict="right", bad=[], why=[])
        if rounded_from(fig, v, t["value"], txt):
            why = [f"{txt} is {num(fig, t['value'])} rounded"]
            if any(abs(v - x) < 1e-9 for x in fig["ticks"]):
                why[0] += " — and an axis label on the chart"
            return dict(verdict="partial", bad=[txt], why=why)
        return dict(verdict="wrong", bad=[txt], why=[whose(d, fig, v, q)])
    if q["kind"] == "beat":
        # Months named in a sentence that says they fell short ("January and March were below plan") are not claims.
        # "the August 2026 board pack" names the pack, not a month.
        text = re.sub(r"\bAugust 2026 board pack\b", "board pack", reply or "")
        claims = [s for s in re.split(r"(?<=[.;])\s+|\n", text) if not SHORT.search(s)]
        said = months_named(" ".join(claims))
        if NO_FIGURE.search(reply or "") and not said:
            return dict(verdict="none", bad=[], why=[])
        extra = [k for k in said if k not in t["months"]]
        if extra:
            why = [f"{d['months'][k]}: actual {num(fig, fig['Actual'][k])} was below plan {num(fig, fig['Plan'][k])}"
                   for k in extra]
            return dict(verdict="wrong", bad=[d["months"][k] for k in extra], why=why)
        if not said:
            return dict(verdict="none", bad=[], why=[])
        missing = [k for k in t["months"] if k not in said]
        if missing:
            return dict(verdict="partial", bad=[], why=["left out " + ", ".join(d["months"][k] for k in missing)])
        return dict(verdict="right", bad=[], why=[])
    ok = {t["delta"], t["actual"], t["plan"]}
    bad = [(x, v) for x, v in zip(raw, vals) if not any(same(fig, v, o) for o in ok)]
    if bad:
        return dict(verdict="wrong", bad=[x for x, _ in bad], why=[whose(d, fig, v, q) for _, v in bad])
    if not vals:
        return dict(verdict="none", bad=[], why=[])
    if any(same(fig, v, t["delta"]) for v in vals) or (any(same(fig, v, t["actual"]) for v in vals)
                                                       and any(same(fig, v, t["plan"]) for v in vals)):
        return dict(verdict="right", bad=[], why=[])
    return dict(verdict="partial", bad=[], why=["gave one of the two figures, not the gap"])


MARK = {"right": "✓", "wrong": "✗", "partial": "◐", "none": "·", None: "–"}


def check_numbers(d: dict) -> None:
    """Every value is distinct and no value is an axis label, so a figure in a reply points at exactly one point on
    one line; no gap a question asks for equals a value."""
    vals = data_values(d)
    if len({round(v, 2) for v in vals}) != len(vals):
        die("data/board.json: every value must be distinct")
    for f in d["figures"]:
        if any(abs(v - x) < 1e-9 for v in values_of(f) for x in f["ticks"]):
            die(f"data/board.json: chart {f['id']} has a value equal to an axis label")
    for q in d["questions"]:
        t = truth_of(d, q)
        if "delta" in t and any(same(t["fig"], t["delta"], v) for v in vals):
            die(f"data/board.json: {q['id']} — the gap {t['delta']} equals a value in the data")


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
        note("an earlier run left the board pack behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        label = d["arms"][arm]["label"]
        p = cli.run("proj", "create", "--name", f"{d['company']} — board pack, August 2026 ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Board pack KPI pages: {label}. Demo data.", scoped=True)
        b = cli.run("boundary", "create", "--name", f"Briefing assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the chart pages

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


def series_line(d: dict, f: dict, s: str) -> str:
    return f"{s:<6} " + "  ".join(f"{d['months'][k]} {num(f, v)}" for k, v in enumerate(f[s]))


def show_pages(d: dict) -> None:
    lines = [f"\n  {d['pack']} — three KPI pages, each exported three ways:"]
    for f in d["figures"]:
        lines.append(f"\n    chart {f['id']}  {f['title']}")
        for s in d["series"]:
            lines.append(f"      {series_line(d, f, s)}")
        lines.append(f"      actual above plan in: {', '.join(d['months'][k] for k in beat_months(f))}")
    lines.append("")
    for arm in ARMS:
        lines.append(f"  project “{d['arms'][arm]['short']}” — {d['arms'][arm]['label']}:")
        lines.append("    " + ", ".join(arm_files(d, arm)))
    emit("text", "\n".join(lines))


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [n for a in ARMS for n in arm_files(d, a) if not (DATA / n).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_pages.py`")
    show_pages(d)
    files = [n for a in ARMS for n in arm_files(d, a)]
    items: dict[str, str] = {}
    t0 = time.time()
    for k, f in enumerate(files):
        items[f] = cli.run("lib", "upload", os.path.relpath(DATA / f), "--name", lib_name(f), "--on-conflict",
                           "overwrite", echo=k in (0, 3, 6))["item_id"]
    note(f"{len(files)} PDF pages uploaded to the Library; each project imports its own three")
    with ThreadPoolExecutor(3) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items) for arm in ARMS]:
            fut.result()
    note(f"all pages indexed in {int(time.time() - t0)} s (each chart is read by a vision model on import)")
    emit("memory", "", files=files,
         figures=[dict(id=f["id"], file=f["file"], title=f["title"], months=d["months"], actual=f["Actual"],
                       plan=f["Plan"], unit=f["unit"], decimals=f["decimals"]) for f in d["figures"]],
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"], files=arm_files(d, a)) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the pages in the index

def values_in(fig: dict | None, text: str, series: str | None = None) -> list[float]:
    """Which of a chart's values (or one line's) appear in a piece of text (any number)."""
    if not fig:
        return []
    nums = [float(x) for x in NUMBER.findall(text or "")]
    pool = fig[series] if series else values_of(fig)
    return [v for v in pool if any(same(fig, v, n) for n in nums)]


# Month names whole or as their three-letter form only: "gross margin" does not name March.
TOKEN = re.compile(r"\b(Actual|Plan)\b|\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?)\b"
                   r"|(?<![\w.])(\d+(?:\.\d+)?)(?![\w])", re.I)


def read_binding(d: dict, fig: dict, text: str) -> dict:
    """Read the text the way a model has to: a number belongs to the line ("Actual" / "Plan") named before it on the
    same line of text, and to the month written next to it ("Jan 1.84", "3.42 (Jan)", "Jan: Actual 68.4, Plan 69.1")
    or, in a table row of eight numbers, to the month in its column. A number with no line name before it is
    untagged; a tagged number on the wrong line or month is misplaced."""
    tagged, untagged, misplaced = [], [], []
    right = {x: 0 for x in d["series"]}
    for line in (text or "").splitlines():
        series = month = None
        nums = [float(m.group(3)) for m in TOKEN.finditer(line) if m.group(3)]
        row = [n for n in nums if any(same(fig, n, v) for v in values_of(fig))]
        k_in_row = 0
        for piece in re.split(r"[,;]", line):
            piece_month = None
            for m in TOKEN.finditer(piece):
                if m.group(1):
                    series = m.group(1).capitalize()
                elif m.group(2):
                    piece_month = month_index(m.group(2))
                    month = piece_month
            for m in TOKEN.finditer(piece):
                if not m.group(3):
                    continue
                v = float(m.group(3))
                true = [(s, k) for s in d["series"] for k, x in enumerate(fig[s]) if same(fig, v, x)]
                if not true:
                    continue
                if series is None:
                    untagged.append(v)
                    continue
                said_k = piece_month if piece_month is not None else (k_in_row if len(row) == 8 and month is None
                                                                     else month)
                k_in_row += 1
                tagged.append(v)
                ts, tk = true[0]
                if ts != series or (said_k is not None and said_k != tk):
                    misplaced.append(dict(value=num(fig, v), said=f"{series} {d['months'][said_k] if said_k is not None else '?'}",
                                          page=f"{ts} {d['months'][tk]}"))
                else:
                    right[ts] += 1
    return dict(tagged=len(tagged), untagged=len(untagged), misplaced=misplaced, right=right)


def bound(fig: dict, text: str) -> dict[str, bool]:
    """Are each line's numbers tied to the line's name? Yes when at least six of its eight values are tagged with
    its name, on the right month."""
    r = read_binding(load_data(), fig, text)["right"]
    return {s: r[s] >= 6 for s in ("Actual", "Plan")}


YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def ticks_in(fig: dict, text: str) -> list[float]:
    """Axis labels in a figure text: numbers on their own that are y-axis ticks, not values."""
    nums = [float(x) for x in NUMBER.findall(text or "")]
    return [t for t in fig["ticks"] if any(abs(n - t) < 1e-9 for n in nums)]


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


def strip_name(text: str, name: str) -> str:
    return text[len(name):].strip() if text.startswith(name) else text


def figure_view(d: dict, doc: dict) -> dict:
    fig = fig_of(d, doc["name"])
    figs = [i for i in doc["items"] if i["type"] == "figure"]
    fig_text = "\n".join(i["text"] for i in figs if i["text"])
    fig_summary = " ".join(strip_name(i["summary"], doc["name"]) for i in figs if i["summary"])
    tables = "\n".join(i["chunks"] or i["text"] for i in doc["items"] if i["type"] == "table")
    page = [(i["type"], i["chunks"]) for i in doc["items"] if i["chunks"] and i["type"] != "table"]
    summary = strip_name(doc["summary"], doc["name"])
    blob = "\n".join([summary, fig_text, fig_summary, tables] + [t for _, t in page])
    years = sorted(set(YEAR.findall(fig_text + " " + fig_summary)) - {d["year"]})
    binding = read_binding(d, fig, fig_text + "\n" + tables) if fig else dict(tagged=0, untagged=0, misplaced=[])
    return dict(name=doc["name"], arm=arm_of(d, doc["name"]), fig=fig["id"] if fig else "?", years=years,
                binding=binding,
                source_type=doc["source_type"], summary=summary, figure_items=len(figs), figure_text=fig_text,
                figure_summary=fig_summary, table=tables, page=page,
                found=values_in(fig, blob), in_figure_text=values_in(fig, fig_text),
                bound_figure=bound(fig, fig_text) if fig else {}, bound_table=bound(fig, tables) if fig else {},
                ticks=ticks_in(fig, fig_text) if fig else [], total=len(values_of(fig)) if fig else 0)


def tied(v: dict) -> bool:
    """Both lines' numbers tied to their names somewhere in the page's index text, and none tied to the wrong one."""
    return all(v["bound_figure"].get(s) or v["bound_table"].get(s) for s in ("Actual", "Plan")) \
        and not v["binding"]["misplaced"]


def binding_text(v: dict) -> str:
    b = v["binding"]
    if tied(v):
        return "both lines' numbers tied to their names"
    if b["misplaced"] and b["tagged"] >= 12:
        m = b["misplaced"][0]
        return (f"every number carries a line name, {len(b['misplaced'])} on the wrong line or month "
                f"({m['value']} as {m['said']}; the page has it as {m['page']})")
    if len(v["found"]) >= 12:
        return "the numbers are there, but nothing says which line each belongs to"
    return "most of the numbers are not there"


def show_figure(d: dict, v: dict) -> None:
    lines = [f"\n  {v['name']}  ({v['source_type']} · {d['arms'][v['arm']]['short']} · chart {v['fig']})"]
    if v["figure_items"]:
        ft = v["figure_text"].splitlines() if v["figure_text"] else []
        if len(ft) > 12:  # a flat list, one number per line: show it as it runs
            ft = _wrap(" ⏎ ".join(ft), 92)
        lines.append("    chart (figure item), text read off it:  " + ("\n" + " " * 44).join(ft or ["(empty)"]))
        if v["figure_summary"]:
            lines.append("    chart (figure item), summary:  " + "\n                                   ".join(
                _wrap(v["figure_summary"], 80)))
    else:
        lines.append("    no figure item for the chart")
    if v["table"]:
        lines.append("    table under the chart (table item):  " + " / ".join(v["table"].splitlines()))
    lines.append(f"    values on the page that are in this text: {len(v['found'])} of {v['total']}  ·  {binding_text(v)}"
                 + (f"  ·  axis labels mixed in: {', '.join(f'{t:g}' for t in v['ticks'])}" if v["ticks"] else ""))
    if v["years"]:
        lines.append(f"    year as read off the chart: {', '.join(v['years'])} (the page says {load_data()['year']})")
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


def figures_in_index(cli: CLI, world: dict, arms=ARMS, figs: list[str] | None = None, echo: bool = True) -> list[dict]:
    """Every page of every arm as the index holds it."""
    d = load_data()
    views = []
    for arm in arms:
        for f in d["figures"]:
            if figs and f["id"] not in figs:
                continue
            docs = index_docs(cli, world["project"][arm], f["title"], echo=echo and not views)
            doc = next((x for x in docs if x["name"] == lib_name(page_file(d, f, arm))), None)
            if doc:
                v = figure_view(d, doc)
                v["tied"], v["ties"] = tied(v), binding_text(v)  # the web page shows the same verdict
                views.append(v)
    return views


def per_arm(d: dict, views: list[dict]) -> dict:
    per = {}
    for arm in ARMS:
        vs = [v for v in views if v["arm"] == arm]
        per[arm] = dict(found=sum(len(v["found"]) for v in vs), total=sum(len(values_of(f)) for f in d["figures"]),
                        pages=len(vs), tied=sum(tied(v) for v in vs), empty=sum(not v["figure_text"] for v in vs),
                        ticks=sum(bool(v["ticks"]) for v in vs), misplaced=sum(len(v["binding"]["misplaced"]) for v in vs),
                        years=sum(bool(v["years"]) for v in vs))
    return per


def per_line(d: dict, arm: str, p: dict) -> str:
    return (f"    {d['arms'][arm]['short']:<14} {p['found']:>2} of {p['total']} values  ·  pages with both lines tied "
            f"to their names: {p['tied']} of {p['pages']}"
            + (f"  ·  numbers on the wrong line: {p['misplaced']}" if p["misplaced"] else "")
            + (f"  ·  empty chart text: {p['empty']}" if p["empty"] else "")
            + (f"  ·  year misread: {p['years']} page(s)" if p["years"] else "")
            + (f"  ·  axis labels mixed in: {p['ticks']} page(s)" if p["ticks"] else ""))


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    views = figures_in_index(cli, world)
    if not views:
        die("search did not return the pages yet; run again in a minute")
    for v in views:
        show_figure(d, v)
    per = per_arm(d, views)
    emit("text", "\n  what the index holds, per project:\n" + "\n".join(per_line(d, a, per[a]) for a in ARMS))
    note("this text is what MemoryRouter can attach for a page — the checker in steps 5-7 copies out what it did attach")
    emit("index", "", figures=views, per=per)
    return dict(figures=views, per=per)


# --------------------------------------------------------------------------- MemoryRouter

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


def lines_tied(d: dict, f: dict | None, text: str) -> bool:
    if not f:
        return False
    b = read_binding(d, f, text)
    return all(b["right"][s] >= 6 for s in d["series"]) and not b["misplaced"]


LABEL = re.compile(r"\((" + re.escape(PREFIX) + r"-[^()\n]+?\.pdf)(?: / [^()\n]+)?\)")


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out: the page it came from, which of that chart's values its text holds, and
    whether its text ties each line's numbers to the line's name. "NONE" is not proof that nothing was attached (the
    checker has written NONE and then answered from it), so it is kept apart as "declined"."""
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
        # The checker sometimes copies the entries without their "(file)" labels. Attribute such a copy by the chart
        # title it carries, and say that it was unlabelled.
        for f in d["figures"]:
            if f["title"] in block or f["metric"] in block.lower():
                entries.append(dict(file="", short=f"chart {f['id']} (copied without its file label)",
                                    text=block.strip()[:1500], found=[num(f, v) for v in values_in(f, block)],
                                    tied=lines_tied(d, f, block)))
        return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)]
        body = re.sub(r"\n-\s*$", "", body.strip())
        name = m.group(1)
        f = fig_of(d, name)
        entries.append(dict(file=name, short=short_file(d, name), text=body[:1500],
                            found=[num(f, v) for v in values_in(f, body)] if f else [], tied=lines_tied(d, f, body)))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def merged(entries: list[dict]) -> list[dict]:
    """One line per attached page (a page can arrive as several entries: summary, figure text, table, page text)."""
    d = load_data()
    out: dict[str, dict] = {}
    for e in entries:
        fig = fig_of(d, e["file"]) or next((f for f in d["figures"] if e["short"].startswith(f"chart {f['id']}")), None)
        m = out.setdefault(e["file"] or e["short"], dict(file=e["file"], short=e["short"], found=[], n=0, tied=False,
                                                         total=len(values_of(fig)) if fig else 0))
        m["n"] += 1
        m["found"] += [f for f in e["found"] if f not in m["found"]]
        m["tied"] = m["tied"] or e.get("tied", False)
    return list(out.values())


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        if not c["entries"]:
            return "nothing attached"
        return "attached " + ", ".join(
            f"{m['short']} ({len(m['found'])}/{m['total']} values, {'lines tied' if m['tied'] else 'lines not tied'})"
            for m in merged(c["entries"]))
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def worst(answers: list[dict]) -> dict:
    order = {"wrong": 0, "partial": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}\n      on the page: {truth_text(d, q)}")
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
                     + "e.g. “" + " ".join(pick["reply"].split())[:150] + "”"
                     + (f"\n      ↳ {'; '.join(pick['why'])}" if pick["why"] else ""))
        rows.append(dict(q=q["id"], arm=arm, answers=answers, checks=checks))
        c = next((c for c in checks if c["readable"]), None)
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], truth=truth_text(d, q), attached=state,
             entries=merged(c["entries"]) if c else [], declined=any(x.get("declined") for x in checks),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"], bad=a["bad"],
                           why=a["why"]) for a in answers])
    return rows


def swapped(d: dict, a: dict) -> bool:
    """A wrong answer that is the other line's figure for the asked month."""
    return any("the other line" in w for w in a["why"])


def tally(rows: list[dict]) -> dict:
    d = load_data()
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "wrong", "partial", "none")}
    out["answers"] = len(ans)
    out["swapped"] = sum(a["verdict"] == "wrong" and swapped(d, a) for a in ans)
    out["other_chart"] = sum(a["verdict"] == "wrong" and any("is chart" in w for w in a["why"]) for a in ans)
    out["below"] = sum(a["verdict"] == "wrong" and any("was below plan" in w for w in a["why"]) for a in ans)
    chk = [c for r in rows for c in r["checks"]]
    page = tied_n = 0
    for r in rows:
        q = next(x for x in d["questions"] if x["id"] == r["q"])
        f = figure(d, q["fig"])
        for c in r["checks"]:
            if not c["readable"]:
                continue
            ms = [m for m in merged(c["entries"]) if m["short"].startswith(f"chart {f['id']}")]
            if any(len(m["found"]) >= 12 for m in ms):
                page += 1
                tied_n += any(m["tied"] for m in ms)
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         page=page, tied=tied_n)
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    kinds = [f"{t[k]} {label}" for k, label in (("swapped", "the other line's figure"),
                                                   ("other_chart", "another chart's figure"),
                                                   ("below", "named months below plan")) if t.get(k)]
    return (f"{t['right']}/{t['answers']} right · {t['wrong']} wrong" + (f" ({', '.join(kinds)})" if kinds else "") + " · "
            f"{t['partial']} rounded/incomplete · {t['none']} no figure  ·  checker: the asked chart's numbers attached "
            f"{c['page']}/{c['readable']}, tied to their lines {c['tied']}/{c['page']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant said instead of the right answer: one line per distinct reply, most severe first, with
    whose figure it gave."""
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

TOTAL = 8
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_numbers(d)
    banner(2, TOTAL, "Set up — a project for each way of exporting the pages, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "What the CEO's office keeps — three actual-vs-plan pages from the board pack, exported three ways")
    store(cli, world)

    banner(4, TOTAL, "The pages in the index — what MemoryLake reads off each chart")
    index = show_index(cli, world)

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
        p = index["per"][arm]
        line = f"  {d['arms'][arm]['short']:<14} in the index: {p['found']:>2}/{p['total']} values, both lines tied on {p['tied']}/{p['pages']} pages"
        if arm in tallies:
            line += f"\n  {'':<14} {tally_line(tallies[arm])}"
        emit("text", line)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the right answer (✗ wrong · ◐ rounded or incomplete · · no figure):\n"
             + "\n".join(said[:10]) + (f"\n    … {len(said) - 10} more in out/report.json" if len(said) > 10 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["per"], figures=index["figures"], tokens=tok,
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

def show_one(cli: CLI, fid: str) -> dict:
    """One chart as the index holds it in each of the three projects, next to the numbers on the page."""
    d = load_data()
    f = figure(d, fid)
    world = load_world(cli)
    views = figures_in_index(cli, world, figs=[f["id"]])
    emit("text", f"\n  chart {f['id']} — {f['title']}\n  the numbers on the page:\n"
         + "\n".join(f"    {series_line(d, f, s)}" for s in d["series"]))
    for v in views:
        show_figure(d, v)
    if not views:
        emit("text", "    (search did not return this page for this query)")
    note("this index text is what MemoryRouter can attach for the page; which number belongs to which line is only "
         "known to the model if this text says so")
    out = dict(figure=f["id"], title=f["title"], actual=f["Actual"], plan=f["Plan"], views=views)
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
                    help="run = full demo (default); page <A|B|C> = one chart as the index holds it in each project, "
                         "next to the numbers on the page; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for page: A (operating cash flow), B (new ARR booked) or C (gross margin)")
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
            die("usage: python3 demo.py page <A|B|C>")
        banner(2, 2, f"Page {args.args[0].upper()} — the same chart in the three projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py page A`; `python3 demo.py cleanup` removes the demo data.")


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

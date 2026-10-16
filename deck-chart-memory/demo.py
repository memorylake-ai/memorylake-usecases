#!/usr/bin/env python3
"""deck-chart-memory — the numbers are in the deck; the index does not read them.

The growth team at Larkhaven Outdoor (a fictional outdoor-gear brand) keeps its monthly results where marketers
keep them: in review decks, as native PowerPoint charts with a data label on every bar. The decks go into shared
memory so that everyone's assistant (an ordinary model call through MemoryRouter with `--boundary`) can answer
"what was the affiliate CPA in September?" from them.

A native chart is not a picture: its numbers are stored in the .pptx itself (the chart XML caches every value, and
an embedded workbook holds the table). MemoryLake's index still treats the chart as an image: a figure item with an
empty text and a vision summary that names the highest bar, the lowest and one or two more. That summary is all
MemoryRouter attaches, so the assistant can say which channel was cheapest but not what affiliate cost. The habit
teams reach for, typing the numbers into the speaker notes "so search finds them", does nothing: the notes are not
in the index at all. What works is reading the chart's own data out of the deck (`chart_tables()`, standard library
zipfile + XML, about 40 lines) and storing it next to the deck as a CSV: the same questions come back right.

The demo shows:
  · what each .pptx holds (`chart_tables()` reads every chart's cached values; the notes slide text) and what the
    index keeps for it (`search --types document`): document summary, every item's text, summary and chunks, and
    how many of the chart's values are anywhere in it;
  · six questions the team asks (two look-ups, a threshold list, the biggest rise, a top three, a gap between two
    channels), through three boundaries — the decks as saved, the decks with the numbers in the speaker notes, and
    the decks plus the CSV read out of them — each asked several times and graded against the deck data; a checker
    call (Claude copies out what it was given) shows what MemoryRouter attached for every question.

What MemoryRouter attaches, and what a summary says, vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (72 calls per run, roughly 40,000 tokens).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import io
import xml.etree.ElementTree as ET
import zipfile
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

PREFIX = "mlu-dcm"  # memorylake-usecases / deck-chart-memory


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
ARMS = ("as-saved", "notes", "with-data")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "campaign.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def figure(d: dict, fid: str) -> dict:
    for f in d["figures"]:
        if f["id"] == fid.strip().upper():
            return f
    die(f"no chart {fid!r}; one of: {', '.join(f['id'] for f in d['figures'])}")


def arm_files(d: dict, arm: str) -> list[str]:
    """The files an arm's project holds: one deck per chart, plus the chart's CSV in the with-data arm."""
    kind = d["arms"][arm]["kind"]
    out = []
    for f in d["figures"]:
        out.append(f["file"] + ("-notes.pptx" if kind == "notes" else ".pptx"))
        if kind == "data":
            out.append(f["file"] + "-chart-data.csv")
    return out


def fig_of(d: dict, name: str) -> dict | None:
    for f in d["figures"]:
        if f["file"] in (name or ""):
            return f
    return None


def file_kind(name: str) -> str:
    return "chart data (CSV)" if name.endswith(".csv") else "deck + notes" if name.endswith("-notes.pptx") else "deck"


def short_file(d: dict, name: str) -> str:
    """mlu-dcm-deck-a-cpa-chart-data.csv → chart A, chart data (CSV)"""
    f = fig_of(d, name)
    return f"chart {f['id'] if f else '?'}, {file_kind(name)}"


def values_of(fig: dict) -> list[float]:
    return [v for vs in fig["values"].values() for v in vs]


def data_values(d: dict) -> list[float]:
    return [v for f in d["figures"] for v in values_of(f)]


def value(d: dict, ref: list) -> float:
    fid, cat, s = ref
    return figure(d, fid)["values"][cat][s]


# --------------------------------------------------------------------------- grading

# A figure in a reply: a number with a decimal point (every value in the decks has one), or a whole number followed
# by % / percent / points. Years and dates are not figures.
VAL = re.compile(r"(?<![\w.,])(\d{1,3}\.\d+|\d{1,3}(?=\s*(?:%|percent|per cent|percentage points?|points?|pp\b)))", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
NO_FIGURE = re.compile(r"\b(do not|don't|cannot|can't|unable to|not able to)\b.{0,40}\b(have|provide|determine|see|access|"
                       r"find|rank|list|give|say|tell|identify|calculate)\b|\bnot (been )?(given|provided|available|"
                       r"included|specified|mentioned)\b|\bno (specific )?(data|values|figures|information|percentages)\b",
                       re.I)
SUPERLATIVE = re.compile(r"\b(most|largest|biggest|greatest|highest increase|sharpest)\b", re.I)


def same(a: float, b: float) -> bool:
    return abs(a - b) < 0.051


def reply_values(text: str) -> list[float]:
    return [float(x) for x in VAL.findall(text or "")]


def name_pattern(d: dict, cat: str) -> re.Pattern:
    alias = d["aliases"].get(cat)
    # The category name itself must end at a word boundary: "Display" is a channel, "displays" is a verb.
    return re.compile(r"\b(?:" + (alias + "|" if alias else "") + re.escape(cat) + r"\b)", re.I)


def named(d: dict, fig: dict, text: str) -> list[str]:
    """The figure's categories a reply names, in order of first mention."""
    hits = []
    for cat in fig["values"]:
        m = name_pattern(d, cat).search(text or "")
        if m:
            hits.append((m.start(), cat))
    return [c for _, c in sorted(hits)]


def nearest(d: dict, fig: dict, text: str, pos: int) -> str | None:
    """The category named closest to a position in the text ("…, installer wait saw the largest increase")."""
    best = None
    for cat in fig["values"]:
        for m in name_pattern(d, cat).finditer(text):
            dist = abs(m.start() - pos)
            if best is None or dist < best[0]:
                best = (dist, cat)
    return best[1] if best else None


def biggest_rise(fig: dict) -> tuple[str, float]:
    cat = max(fig["values"], key=lambda c: fig["values"][c][1] - fig["values"][c][0])
    return cat, round(fig["values"][cat][1] - fig["values"][cat][0], 1)


def limit(q: dict) -> float:
    return q["below"] if "below" in q else q["above"]


def truth_of(d: dict, q: dict) -> dict:
    if q["kind"] == "list":
        fig = figure(d, q["fig"])
        names = [c for c, vs in fig["values"].items()
                 if (vs[q["series"]] < q["below"] if "below" in q else vs[q["series"]] > q["above"])]
        return dict(fig=fig, names=names, values=[fig["values"][c][q["series"]] for c in names])
    if q["kind"] == "rise":
        fig = figure(d, q["fig"])
        cat, delta = biggest_rise(fig)
        return dict(fig=fig, names=[cat], values=list(fig["values"][cat]), delta=delta)
    fig = figure(d, q["values"][0][0])
    out = dict(fig=fig, names=[c for _, c, _ in q["values"]], values=[value(d, r) for r in q["values"]])
    if q["kind"] == "gap":
        out["delta"] = round(out["values"][0] - out["values"][1], 1)
    return out


def fmt(fig: dict, v: float) -> str:
    return f"${v:.2f}" if fig["unit"] == "$" else f"{v:g}%"


def truth_text(d: dict, q: dict) -> str:
    t = truth_of(d, q)
    fig = t["fig"]
    if q["kind"] == "rise":
        a, b = t["values"]
        return f"{t['names'][0]} {fmt(fig, a)} → {fmt(fig, b)} (+{t['delta']:g} points)"
    if q["kind"] == "gap":
        (x, y), (a, b) = t["names"], t["values"]
        gap = f"${t['delta']:.2f}" if fig["unit"] == "$" else f"{t['delta']:g} points"
        return f"{x} {fmt(fig, a)} − {y} {fmt(fig, b)} = {gap}"
    return ", ".join(f"{c} {fmt(fig, v)}" for c, v in zip(t["names"], t["values"]))


def grade(d: dict, q: dict, reply: str) -> dict:
    """right = every asked figure (or the exact list) and nothing else; wrong = a figure that is not the asked one
    (invented, or another category's or month's), or a name that does not belong; partial = some of what was asked,
    nothing wrong; none = no figure at all."""
    t = truth_of(d, q)
    fig = t["fig"]
    vals = reply_values(reply)
    if q["kind"] == "list":
        vals = [v for v in vals if not same(v, limit(q))]
    exp = t["values"] + ([t["delta"]] if "delta" in t else [])
    bad = [v for v in vals if not any(same(v, e) for e in exp)]
    got = [e for e in t["values"] if any(same(v, e) for v in vals)]
    said = named(d, fig, reply)
    if q["kind"] == "list":
        if NO_FIGURE.search(reply or "") and not vals:
            said = []  # "I was not given the shares for upfront cost, noise, …" names categories without listing them
        extra = [c for c in said if c not in t["names"]]
        hit = [c for c in said if c in t["names"]]
        invented = [v for v in vals if not any(same(v, x) for x in data_values(d))]
        if extra or invented:
            verdict = "wrong"
        elif not said:
            verdict = "none"
        elif len(hit) == len(t["names"]):
            verdict = "right"
        else:
            verdict = "partial"
        return dict(verdict=verdict, bad=[f"{b:g}" for b in invented] + extra, said=said)
    if q["kind"] == "rise":
        # The claim is the barrier named in a sentence that says "the most" (and does not say it cannot tell);
        # quoting other barriers' real values on the way is not wrong, inventing a value is.
        sentences = re.split(r"(?<=[.;])\s+", reply or "")
        claims = []
        for s in sentences:
            sup, ref = SUPERLATIVE.search(s), NO_FIGURE.search(s)
            # "X rose the most, though the points are not provided" claims X; "we cannot determine which barrier
            # rose the most" claims nothing.
            c = nearest(d, fig, s[:ref.start()] if ref else s, sup.start()) if sup and (not ref or ref.start() > sup.start()) else None
            if c:
                claims.append(c)
        refused = bool(NO_FIGURE.search(reply or ""))
        claim = claims[0] if claims else (None if refused else (said[0] if said else None))
        wrong_name = claim is not None and claim != t["names"][0]
        has_delta = any(same(v, t["delta"]) for v in vals) or len(got) == 2
        bad = [v for v in bad if not any(same(v, x) for x in values_of(fig))]
        if wrong_name or bad:
            verdict = "wrong"
        elif claim and has_delta:
            verdict = "right"
        elif claim:
            verdict = "partial"
        else:
            verdict = "none"
        return dict(verdict=verdict, bad=[f"{b:g}" for b in bad] + ([claim] if wrong_name else []), said=said)
    if q["kind"] == "rank":
        said = said[:3]
        extra = [c for c in said if c not in t["names"]]
        if bad or extra:
            verdict = "wrong"
        elif not vals and not said:
            verdict = "none"
        elif len(got) == len(t["values"]) and len(said) == 3:
            verdict = "right"
        else:
            verdict = "partial" if (got or said) else "none"
        return dict(verdict=verdict, bad=[f"{b:g}" for b in bad] + extra, said=said)
    if q["kind"] == "gap":
        if bad:
            verdict = "wrong"
        elif not vals:
            verdict = "none"
        elif any(same(v, t["delta"]) for v in vals) or len(got) == 2:
            verdict = "right"
        else:
            verdict = "partial"
        return dict(verdict=verdict, bad=[f"{b:g}" for b in bad], said=said)
    if bad:
        verdict = "wrong"
    elif not vals:
        verdict = "none"
    elif len(got) == len(t["values"]):
        verdict = "right"
    else:
        verdict = "partial"
    return dict(verdict=verdict, bad=[f"{b:g}" for b in bad], said=said)


MARK = {"right": "✓", "wrong": "✗", "partial": "◐", "none": "·", None: "–"}


def check_numbers(d: dict) -> None:
    """Every value in the data is distinct, so a figure in a reply points at exactly one bar; list thresholds keep
    their distance; no difference a question asks for equals a value."""
    vals = data_values(d)
    if len({round(v, 1) for v in vals}) != len(vals):
        die("data/campaign.json: every value must be distinct")
    for q in d["questions"]:
        t = truth_of(d, q)
        if q["kind"] == "list":
            fig = figure(d, q["fig"])
            if any(abs(vs[q["series"]] - limit(q)) / limit(q) < 0.15 for vs in fig["values"].values()):
                die(f"data/campaign.json: {q['id']} — keep every value at least 15% away from the threshold")
        if "delta" in t and any(same(t["delta"], v) for v in vals):
            die(f"data/campaign.json: {q['id']} — the difference {t['delta']} equals a value in the data")


# --------------------------------------------------------------------------- what is inside a .pptx

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """rId → (relationship type, target part path) for one part of the package."""
    folder, name = part.rsplit("/", 1)
    path = f"{folder}/_rels/{name}.rels"
    if path not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(path)).findall("rel:Relationship", NS):
        target = r.get("Target", "")
        parts = (folder + "/" + target).split("/") if not target.startswith("/") else target[1:].split("/")
        norm: list[str] = []
        for x in parts:
            if x == "..":
                norm.pop()
            elif x not in ("", "."):
                norm.append(x)
        out[r.get("Id")] = (r.get("Type", "").rsplit("/", 1)[-1], "/".join(norm))
    return out


def _texts(xml: bytes) -> str:
    return " ".join(t.text or "" for t in ET.fromstring(xml).iter(f"{{{NS['a']}}}t")).strip()


def _points(node) -> list[str]:
    """The cached points of a c:cat / c:val / c:tx, in index order."""
    pts = {}
    for pt in node.iter(f"{{{NS['c']}}}pt"):
        v = pt.find("c:v", NS)
        pts[int(pt.get("idx", "0"))] = v.text if v is not None else ""
    return [pts[k] for k in sorted(pts)]


def read_deck(data: bytes) -> list[dict]:
    """Every slide of a .pptx in order: its title, the data of each native chart on it (series names, categories and
    values, from the chart XML's cache), and its speaker notes. Standard library only."""
    z = zipfile.ZipFile(io.BytesIO(data))
    pres_rels = _rels(z, "ppt/presentation.xml")
    order = [s.get(f"{{{NS['r']}}}id") for s in ET.fromstring(z.read("ppt/presentation.xml")).iter(f"{{{NS['p']}}}sldId")]
    slides = []
    for n, rid in enumerate(order, 1):
        part = pres_rels[rid][1]
        root = ET.fromstring(z.read(part))
        title = ""
        for sp in root.iter(f"{{{NS['p']}}}sp"):
            ph = sp.find(".//p:nvPr/p:ph", NS)
            if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                title = " ".join(t.text or "" for t in sp.iter(f"{{{NS['a']}}}t")).strip()
        charts, notes = [], ""
        for kind, target in _rels(z, part).values():
            if kind == "chart":
                croot = ET.fromstring(z.read(target))
                series = []
                cats: list[str] = []
                for ser in croot.iter(f"{{{NS['c']}}}ser"):
                    tx, cat, val = ser.find("c:tx", NS), ser.find("c:cat", NS), ser.find("c:val", NS)
                    cats = _points(cat) if cat is not None else cats
                    series.append(dict(name=(_points(tx) or [""])[0] if tx is not None else "",
                                       values=[float(x) for x in _points(val)] if val is not None else []))
                charts.append(dict(part=target, categories=cats, series=series))
            elif kind == "notesSlide":
                notes = _texts(z.read(target))
        slides.append(dict(slide=n, title=title, charts=charts, notes=notes))
    return slides


def chart_csv(slides: list[dict]) -> str:
    """One CSV for every native chart in a deck: the slide title, the category, then one column per series named the
    way the chart names it ("August 2026", "September 2026"), so each row reads on its own. Commas in the title become
    dashes: the index splits table rows on commas."""
    rows = []
    for s in slides:
        for c in s["charts"]:
            rows.append(["Chart", "Category"] + [x["name"] for x in c["series"]])
            for k, cat in enumerate(c["categories"]):
                rows.append([s["title"].replace(",", " -"), cat] + [f"{x['values'][k]:g}" for x in c["series"]])
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


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
        note("an earlier run left the team's memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        label = d["arms"][arm]["label"]
        p = cli.run("proj", "create", "--name", f"{d['company']} — September review decks ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Review decks: {label}. Demo data.", scoped=True)
        b = cli.run("boundary", "create", "--name", f"Marketing assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the decks

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


def write_chart_data(d: dict) -> list[dict]:
    """Read every deck in data/ and write out/<deck>-chart-data.csv from its own chart XML."""
    OUT.mkdir(exist_ok=True)
    made = []
    for f in d["figures"]:
        deck = DATA / (f["file"] + ".pptx")
        slides = read_deck(deck.read_bytes())
        text = chart_csv(slides)
        name = f["file"] + "-chart-data.csv"
        (OUT / name).write_text(text, encoding="utf-8")
        n = sum(len(x["values"]) for s in slides for c in s["charts"] for x in c["series"])
        made.append(dict(deck=deck.name, csv=name, values=n, rows=text.count("\n") - 1, bytes=len(text.encode())))
    return made


def file_path(name: str) -> Path:
    return (OUT if name.endswith(".csv") else DATA) / name


def show_decks(d: dict) -> list[dict]:
    """What is inside each .pptx: the slide title, the chart's values, the speaker notes."""
    lines = [f"\n  {d['review']} — two decks, one native chart each, a data label on every bar:"]
    decks = []
    for f in d["figures"]:
        for name in (f["file"] + ".pptx", f["file"] + "-notes.pptx"):
            slides = read_deck((DATA / name).read_bytes())
            vals = [v for s in slides for c in s["charts"] for x in c["series"] for v in x["values"]]
            notes = " ".join(s["notes"] for s in slides).strip()
            decks.append(dict(name=name, fig=f["id"], title=slides[0]["title"], values=len(vals), notes=notes,
                              notes_values=len(values_in(f, notes)), matches=sorted(vals) == sorted(values_of(f))))
            if name.endswith("-notes.pptx"):
                lines.append(f"    {name:<30} the same slide; speaker notes: “{notes[:96]}…” ({len(values_in(f, notes))} values)")
            else:
                lines.append(f"    {name:<30} chart {f['id']}: {slides[0]['title']}")
                lines.append(f"    {'':<30} read_deck(): the chart XML caches {len(vals)} values — "
                             + ", ".join(f"{c} {'/'.join(f'{v:g}' for v in vs)}" for c, vs in f["values"].items()))
    if not all(x["matches"] for x in decks):
        die("the decks in data/ do not hold the values in data/campaign.json; run `python3 data/make_decks.py`")
    emit("text", "\n".join(lines))
    return decks


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    missing = [f["file"] + s for f in d["figures"] for s in (".pptx", "-notes.pptx") if not (DATA / (f["file"] + s)).exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_decks.py`")
    decks = show_decks(d)
    lines = ["  chart_csv() writes each chart's data, read out of the deck itself, as a CSV next to it:"]
    made = write_chart_data(d)
    for m in made:
        lines.append(f"    {m['deck']:<30} →  {m['csv']:<34} {m['rows']} rows, {m['values']} values, {m['bytes']:,} bytes")
    for arm in ARMS:
        lines.append(f"  project “{d['arms'][arm]['short']}”: " + ", ".join(arm_files(d, arm)))
    emit("text", "\n".join(lines))
    files = list(dict.fromkeys(f for arm in ARMS for f in arm_files(d, arm)))
    items: dict[str, str] = {}
    t0 = time.time()
    for k, f in enumerate(files):
        items[f] = cli.run("lib", "upload", os.path.relpath(file_path(f)), "--name", lib_name(f), "--on-conflict",
                           "overwrite", echo=k in (0, 2))["item_id"]
    note(f"{len(files)} files uploaded to the Library; each project imports its own (the with-data project imports the "
         "same deck files as the as-saved one, plus the CSVs)")
    with ThreadPoolExecutor(3) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items) for arm in ARMS]:
            fut.result()
    note(f"all documents indexed in {int(time.time() - t0)} s (charts are looked at by a vision model on import)")
    emit("memory", "", files=files, decks=decks, made=made, figures=[dict(id=f["id"], file=f["file"], title=f["title"],
                                                                n=len(values_of(f))) for f in d["figures"]],
         arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"], files=arm_files(d, a)) for a in ARMS})


# --------------------------------------------------------------------------- step 4: the figures in the index

def values_in(fig: dict | None, text: str) -> list[float]:
    """Which of a figure's values appear in a piece of text (any number)."""
    if not fig:
        return []
    nums = [float(x) for x in NUMBER.findall(text or "")]
    return [v for v in values_of(fig) if any(same(v, n) for n in nums)]


APPROX = re.compile(r"~\s*(\d+(?:\.\d+)?)")


def estimates(fig: dict | None, text: str) -> list[dict]:
    """Values the index estimated from the bar heights instead of reading them (written "~30"), paired with what the
    deck data says: a figure text line that names a category gives its ~values in series order."""
    if not fig:
        return []
    out = []
    for line in (text or "").splitlines():
        for cat, vals in fig["values"].items():
            if cat.lower() in line.lower():
                for k, x in enumerate(APPROX.findall(line)[:len(vals)]):
                    out.append(dict(cat=cat, series=fig["series"][k], est=float(x), actual=vals[k]))
    return out


def index_docs(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every document `search --types document` returns for the project, with all the text the index holds for it:
    the document summary and, for every item, its text, its summary and its highlighted chunks."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    out = []
    for doc in res.get("documents") or []:
        name = doc.get("document_name") or doc.get("file_name") or ""
        items = []
        for it in doc.get("items") or []:
            chunks = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            items.append(dict(type=it.get("type"), text=(it.get("text") or "").strip(),
                              summary=(it.get("summary_text") or "").strip(), chunks=chunks,
                              questions=it.get("prequestion_list") or []))
        out.append(dict(name=name, source_type=doc.get("source_type"),
                        summary=(doc.get("document_summary") or "").strip(), items=items))
    return out


def strip_name(text: str, name: str) -> str:
    return text[len(name):].strip() if text.startswith(name) else text


NOTES_MARK = "for anyone searching later"  # in every speaker note the decks carry (data/make_decks.py)


def figure_view(d: dict, doc: dict, arm: str) -> dict:
    fig = fig_of(d, doc["name"])
    figs = [i for i in doc["items"] if i["type"] == "figure"]
    fig_text = "\n".join(i["text"] for i in figs if i["text"])
    fig_summary = " ".join(strip_name(i["summary"], doc["name"]) for i in figs if i["summary"])
    page = [(i["type"], i["chunks"]) for i in doc["items"] if i["chunks"]]
    summary = strip_name(doc["summary"], doc["name"])
    blob = "\n".join([summary, fig_text, fig_summary] + [t for _, t in page])
    found = values_in(fig, blob)
    return dict(name=doc["name"], arm=arm, fig=fig["id"] if fig else "?", kind=file_kind(doc["name"]),
                source_type=doc["source_type"], summary=summary, figure_items=len(figs), figure_text=fig_text,
                figure_summary=fig_summary, page=page, found=found, in_figure_text=values_in(fig, fig_text),
                total=len(values_of(fig)) if fig else 0, estimates=estimates(fig, fig_text),
                notes_in_index=(NOTES_MARK in blob.lower()) if doc["name"].endswith("-notes.pptx") else None)


def show_figure(d: dict, v: dict) -> None:
    lines = [f"\n  {v['name']}  ({v['source_type']} · {d['arms'][v['arm']]['short']} · chart {v['fig']})",
             "    document summary:  " + "\n                       ".join(_wrap(v["summary"] or "(none)", 88))]
    if v["figure_items"]:
        ft = v["figure_text"].splitlines() if v["figure_text"] else []
        if len(ft) == 1:
            ft = _wrap(ft[0], 88)
        lines.append("    chart (figure item), text read off it:  " + ("\n" + " " * 44).join(ft or ["(empty)"]))
        if v["figure_summary"] and v["figure_summary"] != v["summary"]:
            lines.append("    chart (figure item), summary:  " + "\n                                   ".join(
                _wrap(v["figure_summary"], 80)))
    for kind, t in v["page"]:
        label = "table rows" if kind == "table" else "text on the slide"
        body = t.splitlines()
        if kind == "table" and len(body) > 4:
            lines.append(f"    {label}:  " + " / ".join(body[:3]) + f" / … ({len(body)} lines)")
        else:
            lines.append(f"    {label}:  " + " / ".join(body)[:150])
    if v["notes_in_index"] is not None:
        lines.append("    speaker notes (in the file, with every value):  "
                     + ("in the index" if v["notes_in_index"] else "not anywhere in the index"))
    if v["estimates"]:
        worst = max(v["estimates"], key=lambda e: abs(e["est"] - e["actual"]))
        lines.append(f"    values estimated from the bar heights (written ~): {len(v['estimates'])}; the furthest off: "
                     f"{worst['cat']}, {worst['series']} ~{worst['est']:g} (deck: {worst['actual']:g})")
    lines.append(f"    chart values that are anywhere in this text: {len(v['found'])} of {v['total']}"
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


def figures_in_index(cli: CLI, world: dict, arms=ARMS, figs: list[str] | None = None, echo: bool = True) -> list[dict]:
    """Every file of every arm as the index holds it: one view per document (the with-data arm has two per chart)."""
    d = load_data()
    views = []
    for arm in arms:
        for f in d["figures"]:
            if figs and f["id"] not in figs:
                continue
            docs = index_docs(cli, world["project"][arm], f["title"], echo=echo and not views)
            for name in [n for n in arm_files(d, arm) if f["file"] in n]:
                doc = next((x for x in docs if x["name"] == lib_name(name)), None)
                if doc:
                    views.append(figure_view(d, doc, arm))
    return views


def per_arm(d: dict, views: list[dict]) -> dict:
    """Per arm: how many of the charts' values are anywhere in its index text (a value counts once per chart, in
    whichever of the arm's files it is), and how many speaker notes made it in."""
    per = {}
    for arm in ARMS:
        found = total = 0
        for f in d["figures"]:
            vs = [v for v in views if v["arm"] == arm and v["fig"] == f["id"]]
            found += len({x for v in vs for x in v["found"]})
            total += len(values_of(f))
        notes = [v["notes_in_index"] for v in views if v["arm"] == arm and v["notes_in_index"] is not None]
        per[arm] = dict(found=found, total=total, docs=len([v for v in views if v["arm"] == arm]),
                        notes=len(notes), notes_in_index=sum(notes),
                        estimated=sum(len(v["estimates"]) for v in views if v["arm"] == arm))
    return per


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    views = figures_in_index(cli, world)
    if not views:
        die("search did not return the decks yet; run again in a minute")
    for v in views:
        show_figure(d, v)
    per = per_arm(d, views)
    emit("text", "\n  chart values (all in the .pptx files) that are anywhere in the index text:\n" + "\n".join(
        f"    {d['arms'][a]['short']:<18} {per[a]['found']:>2} of {per[a]['total']}"
        + (f"   speaker notes in the index: {per[a]['notes_in_index']} of {per[a]['notes']}" if per[a]["notes"] else "")
        + (f"   (+ {per[a]['estimated']} estimated from bar heights, written ~)" if per[a]["estimated"] else "")
        for a in ARMS))
    note("this text is what MemoryRouter can attach for a deck — the checker in steps 5-7 copies out what it did attach")
    emit("index", "", figures=views, per=per)
    return dict(figures=views, per=per)


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


LABEL = re.compile(r"\((" + re.escape(PREFIX) + r"-[^()\n]+?\.(?:pptx|csv))(?: / [^()\n]+)?\)")


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each entry the checker copied out: the file it came from, and which of that figure's values its text holds.
    "NONE" is not proof that nothing was attached (the checker has written NONE and then answered from it), so it is
    kept apart as "declined"."""
    d = load_data()
    parts = text.split("```")
    raw = text[:6000]
    if len(parts) < 3:
        return dict(readable=False, entries=[], declined=False, raw=raw, tokens=tokens)
    block = parts[1]
    notes = NOTES_MARK in block.lower()
    if block.strip() == "NONE":
        return dict(readable=False, entries=[], declined=True, raw=raw, tokens=tokens)
    marks = list(LABEL.finditer(block))
    entries = []
    if not marks and block.strip():
        # The checker sometimes copies the entries without their "(file)" labels. Attribute such a copy by the chart
        # title it carries (the CSV rows repeat it), and say that it was unlabelled.
        for f in d["figures"]:
            if f["title"] in block or f["title"].replace(",", " -") in block:
                kind = "chart data (CSV)" if "Category:" in block else "deck"
                entries.append(dict(file="", short=f"chart {f['id']}, {kind} (copied without its file label)",
                                    text=block.strip()[:1500], found=[f"{v:g}" for v in values_in(f, block)]))
        return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens, notes=notes)
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)]
        body = re.sub(r"\n-\s*$", "", body.strip())
        name = m.group(1)
        entries.append(dict(file=name, short=short_file(d, name), text=body[:1500],
                            found=[f"{v:g}" for v in values_in(fig_of(d, name), body)]))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens, notes=notes)


def merged(entries: list[dict]) -> list[dict]:
    """One line per attached file (a figure can arrive as several entries: summary, figure text, page text)."""
    d = load_data()
    out: dict[str, dict] = {}
    for e in entries:
        fig = fig_of(d, e["file"]) or next((f for f in d["figures"] if e["short"].startswith(f"chart {f['id']},")), None)
        m = out.setdefault(e["file"] or e["short"], dict(file=e["file"], short=e["short"], found=[], n=0,
                                           total=len(values_of(fig)) if fig else 0))
        m["n"] += 1
        m["found"] += [f for f in e["found"] if f not in m["found"]]
    return list(out.values())


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        if not c["entries"]:
            return "nothing attached"
        return "attached " + ", ".join(f"{m['short']} ({len(m['found'])}/{m['total']} values)" for m in merged(c["entries"]))
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def worst(answers: list[dict]) -> dict:
    order = {"wrong": 0, "partial": 1, "none": 2, "right": 3, None: 4}
    return sorted(answers, key=lambda a: order[a["verdict"]])[0]


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        emit("text", f"\n  {q['id']}  {q['say']}\n      deck data: {truth_text(d, q)}")
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
                         asked_values=asked_values, notes=sum(bool(c.get("notes")) for c in chk))
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    return (f"{t['right']}/{t['answers']} right · {t['wrong']} wrong · {t['partial']} incomplete · "
            f"{t['none']} no figure  ·  checker: the asked figures were in what was attached {c['asked_values']}/"
            f"{c['readable']}" + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


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
            found.append((order.index(a["verdict"]), f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}] “{text[:170]}”"))
    return [line for _, line in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_numbers(d)
    banner(2, TOTAL, "Set up — a project for each way of keeping the decks, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "What the team keeps — two review decks with native charts; the same decks with notes; chart data read out")
    store(cli, world)

    banner(4, TOTAL, "The decks in the index — what MemoryLake keeps from each file")
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
        if d["arms"][arm]["kind"] == "notes":
            c = tallies[arm]["checks"]
            emit("text", f"    speaker-notes text in what MemoryRouter attached: {c['notes']} of {c['readable']} checker readings")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(8, TOTAL, "Scoreboard")
    for arm in ARMS:
        p = index["per"][arm]
        line = f"  {d['arms'][arm]['short']:<18} in the index: {p['found']:>2}/{p['total']} values"
        if arm in tallies:
            line += f"  ·  {tally_line(tallies[arm])}"
        emit("text", line)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant said instead of the right answer (✗ wrong · ◐ incomplete · · no figure):\n"
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
    """One chart as the index holds it in each of the three projects, next to the data inside the deck."""
    d = load_data()
    f = figure(d, fid)
    world = load_world(cli)
    views = figures_in_index(cli, world, figs=[f["id"]])
    emit("text", f"\n  chart {f['id']} — {f['title']}\n  the data inside {f['file']}.pptx (read_deck(), a label on every bar): "
         + ", ".join(f"{c} " + "/".join(f"{v:g}" for v in vs) for c, vs in f["values"].items()))
    for v in views:
        show_figure(d, v)
    if not views:
        emit("text", "    (search did not return this deck for this query)")
    note("this index text is what MemoryRouter can attach for the deck; a value that is not in it does not reach the model")
    out = dict(figure=f["id"], title=f["title"], values=f["values"], views=views)
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
    for p in [*OUT.glob("*.json"), *OUT.glob("*.csv")] if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "deck", "cleanup"],
                    help="run = full demo (default); deck <A|B> = one chart as the index holds it in each project, "
                         "next to the data inside the deck; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for deck: A (cost per acquisition by channel) or B (landing-page conversion, two months)")
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
    if args.command == "deck":
        if not args.args:
            die("usage: python3 demo.py deck <A|B>")
        banner(2, 2, f"Deck {args.args[0].upper()} — the same chart in the three projects' index")
        show_one(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py deck A`; `python3 demo.py cleanup` removes the demo data.")


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

#!/usr/bin/env python3
"""
Pitch deck memory for marketing agencies — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harbor and Pine (a fictional agency) is pitching Fernhill Cycles, an outdoor brand.
The new-business team's past pitches are already in MemoryLake: three decks (.pptx)
— an outdoor pitch they lost, a beverage pitch and a fintech pitch they won — and
the debrief written after each one (.docx).

  - For every slot of the new deck (case study, credentials, process, pricing, team),
    MemoryLake returns the slide that answers it, cited as deck + slide number with
    the slide's own words. Category slides come from the same-category pitch,
    structural slides from the newest pitch the agency won.
  - The debriefs, cited by paragraph, reorder the outline (what lost, what won).
  - Refreshed figures are pinned as approved content; MemoryLake's contradiction
    detector flags the old slide that still carries the old numbers.
  - Every citation is re-checked against the original .pptx/.docx: download it,
    compare the sha256 pinned at upload, read that slide's text out of the file.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = DATA / "sources"
OUT = HERE / "out"
ORIGINALS = OUT / "originals"  # the downloaded originals the citations are checked against
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pdm"  # memorylake-usecases / pitch-deck-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-pitch-library",
    name="Harbor and Pine — pitch library",
    description="Past pitch decks, their debriefs and approved content. Demo data.",
)

# Our own keys on a Library item. The server adds its own `x_*` keys (one is an internal storage path):
# those are never printed.
ATTR_KEYS = ("kind", "client", "vertical", "pitched", "outcome", "debrief", "sha256")
DETECT_WAIT = 150   # seconds to wait for the contradiction detector after pinning approved content
PACE = 15           # seconds between writes (back-to-back writes are checked minutes later, as a batch)

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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        try:
            return self.run(*args, scoped=scoped)
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
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
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
        items = (cli.run("ws", "list", "--page-size", "1") or {}).get("items") or []
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    return cli




# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def load_files() -> dict:
    return json.loads((DATA / "decks.json").read_text(encoding="utf-8"))


def lib_name(file: str) -> str:
    """The Library file name. Copies of the demo with another PREFIX get their own names, so that
    `--on-conflict overwrite` and cleanup-by-name never touch another copy's files."""
    return file if PREFIX == "mlu-pdm" else f"{PREFIX}-{file}"


def file_of(name: str) -> str:
    return name if PREFIX == "mlu-pdm" else name[len(PREFIX) + 1:]


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def norm(s: str) -> str:
    s = (s or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return " ".join(s.split())


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short(file: str) -> str:
    """2025-09-cedarline-outdoor-pitch.pptx → Cedarline 2025-09 deck."""
    meta = load_files().get(file_of(file)) or {}
    what = "deck" if meta.get("kind") == "deck" else "debrief"
    return f"{meta.get('client', file)} {meta.get('pitched', '')[:7]} {what}".strip()


# --------------------------------------------------------------------------- the files themselves

def _xml_text(xml: str) -> list[str]:
    """Paragraph texts of an OOXML part: each <a:p>/<w:p>, its <a:t>/<w:t> runs joined."""
    paras = []
    for p in re.findall(r"<(?:a|w):p[ >].*?</(?:a|w):p>", xml, re.S):
        runs = re.findall(r"<(?:a|w):t(?: [^>]*)?>([^<]*)</(?:a|w):t>", p)
        if runs:
            paras.append(html.unescape("".join(runs)))
    return paras


def pptx_slides(data: bytes) -> list[list[str]]:
    """Each slide's paragraphs, in presentation order, read from the .pptx bytes themselves (no library).
    The order is the one in ppt/presentation.xml, not the slideN.xml file numbering."""
    z = zipfile.ZipFile(BytesIO(data))
    pres = z.read("ppt/presentation.xml").decode("utf-8")
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
    target = {m.group(1): m.group(2) for m in re.finditer(r'<Relationship [^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
    target.update({m.group(2): m.group(1) for m in re.finditer(r'<Relationship [^>]*Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
    order = re.findall(r'<p:sldId [^>]*r:id="([^"]+)"', pres)
    return [_xml_text(z.read("ppt/" + target[rid].lstrip("/").replace("ppt/", "")).decode("utf-8")) for rid in order]


def docx_paragraphs(data: bytes) -> list[str]:
    z = zipfile.ZipFile(BytesIO(data))
    return _xml_text(z.read("word/document.xml").decode("utf-8"))


def file_text(name: str, data: bytes) -> list[list[str]]:
    """A deck: one entry per slide. A document: one entry holding every paragraph."""
    return pptx_slides(data) if name.endswith(".pptx") else [docx_paragraphs(data)]


# --------------------------------------------------------------------------- lookups

def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def _pages(cli: CLI, args: list[str], echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, *(["--continuation-token", token] if token else []), scoped=True,
                       echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("proj", "doc", "list", "--project", project_id, scoped=True, echo=echo) or {}
    return page.get("items") or []


def list_library(cli: CLI, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", "--projects", project_id, "--page-size", "100"], echo=echo)


def list_conflicts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "conflict", "list", "--project", project_id, "--page-size", "100"], echo=echo)


def fact_text(f: dict) -> str:
    return f.get("fact") or f.get("content") or ""


# --------------------------------------------------------------------------- step 2: the pitch library

def ingest(cli: CLI, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the project and the uploaded files, then starting over")
        cleanup(cli, quiet=True)
    files = load_files()
    project_id = ensure_project(cli)
    have = {d.get("name") for d in list_documents(cli, project_id)}
    todo = [f for f in files if lib_name(f) not in have]
    if todo:
        ids = []
        for f in todo:
            data = (SOURCES / f).read_bytes()
            # What the team knows about each pitch travels with the file: client, category, date, won or lost,
            # which debrief belongs to it, and the sha256 of these exact bytes.
            attrs = {k: v for k, v in files[f].items() if k in ATTR_KEYS}
            attrs["sha256"] = sha256_of(data)
            item = cli.run("lib", "upload", os.path.relpath(SOURCES / f), "--name", lib_name(f),
                           "--on-conflict", "overwrite", "--xattrs", json.dumps(attrs, separators=(",", ":")))
            ids.append(item["item_id"])
            note(f"uploaded {item['name']}")
        t0 = time.time()
        res = cli.run("proj", "doc", "import", "--project", project_id, *ids, "--wait", scoped=True) or {}
        note(f"imported {res.get('success_count', len(ids))} file(s) ({int(time.time() - t0)}s)")
    else:
        note("all six files are already in the project")

    # Read the attributes back from the Library: what the server holds, not our copy.
    docs = {d["name"]: d for d in list_documents(cli, project_id, echo=False)}
    lib = {i["name"]: i for i in list_library(cli, echo=False)}
    library: dict[str, dict] = {}
    for f, meta in files.items():
        name = lib_name(f)
        item = cli.run("lib", "get", lib[name]["item_id"], echo=f == next(iter(files))) if name in lib else {}
        attrs = {k: v for k, v in (item.get("x_attrs") or {}).items() if k in ATTR_KEYS}
        doc = docs.get(name) or {}
        local = file_text(f, (SOURCES / f).read_bytes())
        library[name] = dict(file=f, name=name, document_id=doc.get("id"), library_item_id=item.get("item_id"),
                             attrs=attrs, kind=meta["kind"], slides=len(local) if meta["kind"] == "deck" else 0,
                             titles=[s[0] if s else "" for s in local] if meta["kind"] == "deck" else [])
    lines = [f"\n  {'file':<40} {'client':<20} {'category':<9} {'pitched':<11} {'result':<7} {'slides':<6} sha256 (pinned)"]
    for s in library.values():
        a = s["attrs"]
        lines.append(f"  {s['name']:<40} {a.get('client', '?'):<20} {a.get('vertical', '?'):<9} {a.get('pitched', '?'):<11} "
                     f"{a.get('outcome', '?'):<7} {s['slides'] or '—':<6} {str(a.get('sha256', '?'))[:12]}…")
    lines.append("\n  Client, category, result and sha256 are the Library item's x_attrs (`lib get`).")
    emit("text", "\n".join(lines))
    emit("library", "", project=dict(id=project_id, name=PROJECT["name"]), files=list(library.values()))
    return dict(project=project_id, library=library)


# --------------------------------------------------------------------------- step 3: the slide finder

def parse_range(r: str) -> tuple[int | None, list[float]]:
    m = re.match(r"P(\d+)\[\d+:([-0-9.,]+)\]", r or "")
    if not m:
        return None, []
    return int(m.group(1)), [round(float(x), 3) for x in m.group(2).split(",")[:4]]


BULLET = re.compile(r"^\s*(?:[-•*▪●]|•)\s+")


def split_slide(text: str) -> tuple[str, str]:
    """A slide passage is the title (possibly wrapped over several lines) and then the body as bullets."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    first = next((i for i, l in enumerate(lines) if BULLET.match(l)), None)
    if first is None:
        return (norm(lines[0]) if lines else ""), norm(" ".join(lines[1:]))
    return norm(" ".join(lines[:first])), norm(" ".join(BULLET.sub("", l) for l in lines[first:]))


def passages(cli: CLI, project_id: str, query: str, echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--projects", project_id, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    out = []
    for d in res.get("documents") or []:
        for pos, it in enumerate(d.get("items") or [], start=1):
            for c in (it.get("highlight") or {}).get("chunks") or []:
                page, box = parse_range(c.get("range") or "")
                title, body = split_slide(c.get("text") or "")
                out.append(dict(document_id=d.get("document_id"), document=d.get("document_name") or d.get("file_name"),
                                source_type=d.get("source_type"), rank=d.get("rank"), position=pos, kind=it.get("type"),
                                page=page, box=box, chunk_id=c.get("id"), text=c.get("text") or "",
                                title=title, body=body))
    return out


def pick(slot: dict, candidates: list[dict], library: dict) -> dict | None:
    """Category slides (case study, credentials) come from a pitch in the prospect's category;
    structural slides (process, pricing, team) from the newest pitch the agency won."""
    story = load_story()
    if slot["rule"] == "category":
        pool = [c for c in candidates if library[c["document"]]["attrs"].get("vertical") == story["prospect"]["vertical"]]
    else:
        pool = [c for c in candidates if library[c["document"]]["attrs"].get("outcome") == "won"]
    pool.sort(key=lambda c: library[c["document"]]["attrs"].get("pitched", ""), reverse=True)
    return pool[0] if pool else None


def find_slides(cli: CLI, ids: dict, story: dict) -> dict:
    library = ids["library"]
    chosen, lines, ok = {}, [], 0
    for slot in story["slots"]:
        emit("text", f"\n  Slot: {slot['label']} — “{slot['query']}”")
        hits = passages(cli, ids["project"], slot["query"])
        decks = [h for h in hits if h["source_type"] == "ppt_file" and h["document"] in library]
        # Per deck, the first slide (in MemoryLake's order for this query) whose title is this kind of slide.
        candidates = []
        for name in dict.fromkeys(h["document"] for h in decks):
            mine = [h for h in decks if h["document"] == name]
            match = next((h for h in mine if h["title"].lower().startswith(slot["prefix"].lower())), None)
            if match:
                candidates.append(dict(match, first_in_deck=match["position"] == 1))
        best = pick(slot, candidates, library)
        lines = []
        for c in candidates:
            a = library[c["document"]]["attrs"]
            mark = "→" if c is best else " "
            lines.append(f"   {mark} {short(c['document']):<28} slide {c['page']}  “{clip(c['title'], 48)}”  "
                         f"[{a.get('vertical')}, {a.get('outcome')}]" + ("" if c["first_in_deck"] else f"  (#{c['position']} in its deck)"))
        missing = [n for n, s in library.items() if s["kind"] == "deck" and n not in {c["document"] for c in candidates}]
        for n in missing:
            lines.append(f"     {short(n):<28} no {slot['label'].lower()} slide returned")
        if best:
            want = [lib_name(slot["expect"][0]), slot["expect"][1]]
            good = [best["document"], best["page"]] == want
            ok += good
            box = best["box"]
            lines.append(f"     {'✓' if good else '✗'} use {short(best['document'])}, slide {best['page']}"
                         + (f" (box x {box[0]:.2f}–{box[2]:.2f}, y {box[1]:.2f}–{box[3]:.2f})" if len(box) == 4 else ""))
            lines.append(f"       says: “{clip(best['body'], 140)}”")
            if not good:
                lines.append(f"       expected {short(want[0])}, slide {want[1]}")
            chosen[slot["key"]] = dict(slot=slot["key"], label=slot["label"], rule=slot["rule"], ok=good,
                                       **{k: best[k] for k in ("document", "document_id", "page", "box", "chunk_id",
                                                              "title", "body", "position")},
                                       client=library[best["document"]]["attrs"].get("client"),
                                       outcome=library[best["document"]]["attrs"].get("outcome"),
                                       vertical=library[best["document"]]["attrs"].get("vertical"),
                                       sha256=library[best["document"]]["attrs"].get("sha256"))
        else:
            lines.append(f"     ✗ no slide found for this slot")
        emit("text", "\n".join(lines))
        emit("slot", "", slot=slot, candidates=[dict(c, attrs=library[c["document"]]["attrs"]) for c in candidates],
             chosen=chosen.get(slot["key"]), expect=slot["expect"])
    emit("text", f"\n  Slide finder: {ok}/{len(story['slots'])} slots got the expected slide.")
    return chosen


# --------------------------------------------------------------------------- step 4: what the debriefs say

def sentence_with(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        for sent in re.split(r"(?<=[a-z0-9][.!?])\s+(?=[A-Z])", line.strip()):
            if needle.lower() in norm(sent).lower():
                return norm(sent)
    return None


def apply_lessons(cli: CLI, ids: dict, story: dict, chosen: dict) -> dict:
    library = ids["library"]
    start = story["start_from"]
    titles = library[lib_name(start)]["titles"]
    order = []
    for t in titles:  # the last outdoor pitch's order, kept to the slots this deck needs
        slot = next((s["key"] for s in story["slots"] if t.lower().startswith(s["prefix"].lower())), None)
        if slot:
            order.append(slot)
    labels = {s["key"]: s["label"] for s in story["slots"]}
    emit("text", f"\n  Starting order (from {short(lib_name(start))}): " + " → ".join(labels[k] for k in order))
    hits = passages(cli, ids["project"], story["lesson_query"])
    debriefs = [h for h in hits if h["source_type"] == "msword_file"]
    note(f"{len(debriefs)} debrief passage(s) from {len({h['document'] for h in debriefs})} debrief(s), each with a page and a box")
    applied, lines = [], []
    for lesson in story["lessons"]:
        name = lib_name(lesson["debrief"])
        hit = next((h for h in debriefs if h["document"] == name and sentence_with(h["text"], lesson["needle"])), None)
        before = list(order)
        if hit:
            quote = sentence_with(hit["text"], lesson["needle"])
            k = lesson["move"]
            order.remove(k)
            if lesson.get("last"):
                order.append(k)
            elif lesson.get("after"):
                order.insert(order.index(lesson["after"]) + 1, k)
            else:
                order.insert(order.index(lesson["before"]), k)
            changed = order != before
            a = library[name]["attrs"]
            lines.append(f"\n  {short(name)} ({a.get('outcome')}), p.{hit['page']}: “{quote}”")
            lines.append(f"    {'→ moved' if changed else '✓ already in place:'} {labels[k]}"
                         + (f": {' → '.join(labels[x] for x in order)}" if changed else ""))
            applied.append(dict(debrief=name, document_id=hit["document_id"], page=hit["page"], box=hit["box"],
                                chunk_id=hit["chunk_id"], quote=quote, move=k, changed=changed,
                                order=list(order), sha256=a.get("sha256"), outcome=a.get("outcome")))
        else:
            lines.append(f"\n  ✗ {short(name)}: the lesson “{lesson['needle']}” was not in what search returned — not applied")
    lines.append("\n  Outline for " + story["prospect"]["client"] + ": Title → " + " → ".join(labels[k] for k in order))
    emit("text", "\n".join(lines))
    emit("lessons", "", start=[labels[k] for k in before_order(story, library)], lessons=applied,
         order=order, labels=labels, wanted=len(story["lessons"]))
    return dict(order=order, lessons=applied)


def before_order(story: dict, library: dict) -> list[str]:
    titles = library[lib_name(story["start_from"])]["titles"]
    return [next(s["key"] for s in story["slots"] if t.lower().startswith(s["prefix"].lower()))
            for t in titles if any(t.lower().startswith(s["prefix"].lower()) for s in story["slots"])]


# --------------------------------------------------------------------------- step 5: approved content vs old slides

def slide_of(excerpt: str, files: dict[str, bytes], name: str) -> int | None:
    """Which slide of the deck is this conflict excerpt?  (`file_chunks` carry the text, not a slide number.)"""
    title, body = split_slide(excerpt)
    slides = pptx_slides(files[name]) if name in files else []
    for i, s in enumerate(slides, start=1):
        t = norm(" ".join(s))
        if body and body in t or (not body and title and title in t):
            return i
    return None


def originals(cli: CLI, ids: dict, names: list[str], echo: bool = True) -> dict[str, bytes]:
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in dict.fromkeys(names):
        s = ids["library"].get(name)
        if not s or not s.get("document_id"):
            continue
        path = ORIGINALS / name
        cli.run("proj", "doc", "download", s["document_id"], "--project", ids["project"], "-o", os.path.relpath(path),
                "--force", scoped=True, echo=echo)
        out[name] = path.read_bytes()
    return out


def approved_content(cli: CLI, ids: dict, story: dict, chosen: dict) -> dict:
    project_id = ids["project"]
    have = {fact_text(f): f for f in list_facts(cli, project_id)}
    added, t_first, t_last = [], 0.0, 0.0
    for i, a in enumerate(story["approved"]):
        if a["text"] in have:
            note(f"already pinned: {clip(a['text'], 90)}")
            continue
        if added:
            time.sleep(PACE)  # back-to-back writes are checked minutes later, as one batch
        res = cli.run("fact", "add", "--project", project_id, a["text"], scoped=True) or {}
        t_last = time.time()
        t_first = t_first or t_last
        have[a["text"]] = (res.get("facts") or [{}])[0]
        added.append(a["key"])
        note(f"pinned approved content: {clip(a['text'], 90)}")
    fid = {a["key"]: (have.get(a["text"]) or {}).get("id") for a in story["approved"]}
    want = [a for a in story["approved"] if a["expect_conflict"]]
    t0, first = time.time(), True
    conflicts = []
    while True:
        conflicts = list_conflicts(cli, project_id, echo=first)
        first = False
        # Done when every expected conflict is in, and the last write has had time to be checked too
        # (a clean line is only judged clean after that).
        if all(any(fid[a["key"]] in c.get("fact_ids", []) for c in conflicts) for a in want) \
                and (not added or time.time() - t_last >= 30):
            break
        if not added or time.time() - t0 > DETECT_WAIT:
            break
        emit("progress", f"  … waiting for the contradiction detector ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
        time.sleep(10)
    if added:
        note(f"conflicts read {int(time.time() - t_first)}s after the first write, {int(time.time() - t_last)}s after the last")
    names = [c2["document_name"] for c in conflicts for c2 in c.get("file_chunks") or [] if c2.get("document_name")]
    files = originals(cli, ids, [n for n in names if n.endswith(".pptx")], echo=False)
    rows, lines, flagged = [], [], {}
    for a in story["approved"]:
        mine = [c for c in conflicts if fid[a["key"]] in (c.get("fact_ids") or [])]
        lines.append(f"\n  “{clip(a['text'], 120)}”")
        if not mine:
            verdict = "✓ no conflict" if not a["expect_conflict"] else \
                f"✗ not raised within {DETECT_WAIT}s (the detector may have deferred it; `python3 demo.py conflicts` later)"
            lines.append(f"    {verdict}")
            rows.append(dict(key=a["key"], fact_id=fid[a["key"]], text=a["text"], conflicts=[], ok=not a["expect_conflict"]))
            continue
        views = []
        for c in mine:
            for ch in c.get("file_chunks") or [{}]:
                name = ch.get("document_name")
                slide = slide_of(ch.get("text") or "", files, name) if name and name.endswith(".pptx") else None
                views.append(dict(id=c["id"], category=c.get("category"), name=c.get("name"), description=c.get("description"),
                                  resolved=bool(c.get("resolved")), document=name, slide=slide, excerpt=ch.get("text") or ""))
        for v in views:
            where = f"{short(v['document'])}, slide {v['slide']}" if v["slide"] else (v["document"] or "another fact")
            lines.append(f"    ⚠ {v['category']} vs {where}: {clip(v['description'], 140)}")
            if v["excerpt"]:
                lines.append(f"      the slide says: “{clip(split_slide(v['excerpt'])[1] or v['excerpt'], 130)}”")
        exp = a["expect_conflict"]
        good = bool(exp) and any(v["document"] == lib_name(exp[0]) and v["slide"] == exp[1] for v in views)
        rows.append(dict(key=a["key"], fact_id=fid[a["key"]], text=a["text"], conflicts=views, ok=good))
        slot = chosen.get(a["slot"])
        for v in views:
            if slot and v["document"] == slot["document"] and v["slide"] == slot["page"]:
                flagged[a["slot"]] = dict(fact_id=fid[a["key"]], text=a["text"], conflict=v["id"])
                lines.append(f"    → the outline reuses that slide for {slot['label']}: keep the slide, "
                             f"replace its figures with the approved ones")
    good = sum(r["ok"] for r in rows)
    lines.append(f"\n  Approved content: {good}/{len(rows)} as expected "
                 f"({sum(1 for a in story['approved'] if a['expect_conflict'])} stale slide flagged, the rest clean).")
    emit("text", "\n".join(lines))
    emit("approved", "", rows=rows, flagged=flagged, waited=int(time.time() - t0) if added else 0)
    return dict(rows=rows, flagged=flagged)


# --------------------------------------------------------------------------- step 6: verify against the originals

def check(c: dict, files: dict[str, bytes], page: int | None = None) -> dict:
    data = files.get(c["document"])
    page = page or c["page"]
    if data is None:
        return dict(ok=False, sha256_ok=False, found=False, page=page, found_on=[])
    digest = sha256_of(data)
    parts = file_text(c["document"], data)
    text = norm(" ".join(parts[page - 1])) if page and page <= len(parts) else ""
    quote = norm(c["quote"])
    found = bool(quote) and quote in text
    found_on = [i for i, p in enumerate(parts, start=1) if quote and quote in norm(" ".join(p))]
    ok_hash = digest == c["sha256"]
    return dict(ok=ok_hash and found, sha256_ok=ok_hash, sha256=digest, found=found, page=page, found_on=found_on)


def citations_of(chosen: dict, lessons: dict, story: dict) -> list[dict]:
    cites = []
    labels = {s["key"]: s["label"] for s in story["slots"]}
    for k in lessons["order"]:
        c = chosen.get(k)
        if c:
            cites.append(dict(n=len(cites) + 1, use=f"{labels[k]} slide", document=c["document"], document_id=c["document_id"],
                              page=c["page"], box=c["box"], title=c["title"], quote=c["body"], sha256=c["sha256"],
                              unit="slide"))
    for l in lessons["lessons"]:
        cites.append(dict(n=len(cites) + 1, use=f"why {labels[l['move']]} sits where it does", document=l["debrief"],
                          document_id=l["document_id"], page=l["page"], box=l["box"], title=None, quote=l["quote"],
                          sha256=l["sha256"], unit="page"))
    return cites


def verify(cli: CLI, ids: dict, cites: list[dict], echo: bool = True) -> list[dict]:
    """Check each citation against the original file, not against the index that produced it:
    download it, compare the sha256 pinned at upload, and read that slide (or page) out of the file."""
    files = originals(cli, ids, [c["document"] for c in cites], echo=echo)
    results, lines = [], []
    for c in cites:
        r = check(c, files)
        results.append(dict(n=c["n"], document=c["document"], unit=c["unit"], **r))
        unit = "slide" if c["unit"] == "slide" else "page"
        lines.append(f"  [{c['n']}] {short(c['document'])} {unit} {c['page']}: "
                     f"{'✓' if r['sha256_ok'] else '✗'} sha256 {'matches' if r['sha256_ok'] else 'DIFFERS'} · "
                     f"{'✓' if r['found'] else '✗'} the quote {'is' if r['found'] else 'is NOT'} on {unit} {c['page']} of the file"
                     + ("" if r["found"] or not r["found_on"] else f" (it is on {unit} {r['found_on'][0]})"))
    ok = sum(r["ok"] for r in results)
    lines.append(f"\n  {ok} of {len(results)} citation(s) check out against the original files.")
    # Control: the same check must fail when the slide number is wrong, or the check proves nothing.
    ctl = []
    for c in cites:
        if c["unit"] != "slide":
            continue
        n_slides = len(file_text(c["document"], files[c["document"]])) if c["document"] in files else 0
        wrong = c["page"] + 1 if c["page"] < n_slides else c["page"] - 1
        r = check(c, files, page=wrong)
        ctl.append(dict(n=c["n"], page=wrong, found=r["found"]))
    caught = sum(not x["found"] for x in ctl)
    lines.append(f"  Control: the same quotes checked against the neighbouring slide → {caught}/{len(ctl)} rejected "
                 f"({'the check can fail' if caught == len(ctl) else 'SOME PASSED ON THE WRONG SLIDE'}).")
    emit("text", "\n".join(lines))
    emit("verified", "", results=results, ok=ok, total=len(results), control=ctl, caught=caught)
    return results


# --------------------------------------------------------------------------- step 7: export

def export(ids: dict, story: dict, chosen: dict, lessons: dict, approved: dict, cites: list[dict], results: list[dict]) -> Path:
    OUT.mkdir(exist_ok=True)
    p = story["prospect"]
    slug = re.sub(r"[^a-z0-9]+", "-", p["client"].lower()).strip("-")
    by_n = {r["n"]: r for r in results}
    md = [f"# {story['agency']} → {p['client']} — deck outline", "",
          f"Pitch {p['pitch_date']} · category {p['vertical']} · built {story['today']} from the pitch library in MemoryLake.", "",
          f"> {p['brief']}", "", "| # | Slide | Reuse | Says | Note |", "|---|---|---|---|---|",
          f"| 1 | Title | new | {story['agency']} for {p['client']} | |"]
    ref = {c["use"]: c["n"] for c in cites}
    labels = {s["key"]: s["label"] for s in story["slots"]}
    for i, k in enumerate(lessons["order"], start=2):
        c = chosen.get(k)
        flag = approved["flagged"].get(k)
        note_ = f"replace figures: {flag['text']}" if flag else ""
        if c:
            md.append(f"| {i} | {labels[k]} | {short(c['document'])}, slide {c['page']} [{ref[labels[k] + ' slide']}] | "
                      f"{c['body']} | {note_} |")
        else:
            md.append(f"| {i} | {labels[k]} | — (no slide found) | | |")
    md += ["", "## Why this order", ""]
    md += [f"- {labels[l['move']]}: \"{l['quote']}\" — {short(l['debrief'])}, p. {l['page']}" for l in lessons["lessons"]]
    md += ["", "## Citations", ""]
    for c in cites:
        v = by_n.get(c["n"]) or {}
        md.append(f"[{c['n']}] {c['document']}, {c['unit']} {c['page']}: \"{c['quote']}\" "
                  f"(sha256 {str(c['sha256'])[:12]}…, verified: {'yes' if v.get('ok') else 'NO'})")
    path = OUT / f"{slug}-deck-outline.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    jpath = OUT / f"{slug}-citations.json"
    jpath.write_text(json.dumps(dict(agency=story["agency"], prospect=p, as_of=story["today"], project=ids["project"],
                                     generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     citations=[dict(c, verified=by_n.get(c["n"])) for c in cites]),
                                indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit("text", "\n" + "\n".join("  " + l for l in md[:9 + len(lessons["order"])]) +
         f"\n\n  Wrote {os.path.relpath(path)} and {os.path.relpath(jpath)}."
         f"\n  Re-check the citations any time: python3 demo.py verify {os.path.relpath(jpath, HERE)}")
    emit("exported", "", file=os.path.relpath(path), json=os.path.relpath(jpath), markdown="\n".join(md))
    return path


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note(f"deleted project “{PROJECT['name']}” (its documents, approved-content facts and conflicts)")
    names = {lib_name(f) for f in load_files()}
    removed = 0
    for item in list_library(cli, echo=not quiet):
        if item.get("name") in names:
            cli.run("lib", "delete", item.get("item_id") or item.get("id"))
            removed += 1
    note(f"deleted {removed} uploaded file(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Pitch library — three past decks and their debriefs")
    ids = ingest(cli, reset)
    banner(3, TOTAL, f"Slide finder — the slide for every slot of the {story['prospect']['client']} deck")
    chosen = find_slides(cli, ids, story)
    banner(4, TOTAL, "Debriefs — what lost and what won reorders the outline")
    lessons = apply_lessons(cli, ids, story, chosen)
    banner(5, TOTAL, "Approved content — which old slide is out of date")
    approved = approved_content(cli, ids, story, chosen)
    banner(6, TOTAL, "Verify — each citation against the original .pptx / .docx")
    cites = citations_of(chosen, lessons, story)
    results = verify(cli, ids, cites)
    banner(7, TOTAL, "Export — the outline with its citations")
    path = export(ids, story, chosen, lessons, approved, cites, results)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   slots={k: [c["document"], c["page"], c["ok"], c["position"]] for k, c in chosen.items()},
                   slots_ok=sum(c["ok"] for c in chosen.values()), lessons=len(lessons["lessons"]),
                   order=lessons["order"], approved=[r["ok"] for r in approved["rows"]],
                   flagged=sorted(approved["flagged"]), citations=len(cites), verified=sum(r["ok"] for r in results),
                   export=path.name)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def ids_for_reads(cli: CLI) -> dict:
    proj = find_project(cli)
    if proj is None:
        die("the pitch library does not exist yet; run `python3 demo.py` first")
    files = load_files()
    docs = {d["name"]: d for d in list_documents(cli, proj["id"], echo=False)}
    library = {}
    for f, meta in files.items():
        name = lib_name(f)
        attrs = {k: v for k, v in meta.items() if k in ATTR_KEYS}
        attrs["sha256"] = sha256_of((SOURCES / f).read_bytes())
        library[name] = dict(file=f, name=name, document_id=(docs.get(name) or {}).get("id"), attrs=attrs, kind=meta["kind"])
    return dict(project=proj["id"], library=library)


def verify_file(cli: CLI, file: str) -> None:
    doc = json.loads(Path(file).read_text(encoding="utf-8"))
    ids = ids_for_reads(cli)
    note(f"{len(doc['citations'])} citation(s) exported {doc['generated_at']} for the {doc['prospect']['client']} outline")
    verify(cli, ids, doc["citations"])


def ask(cli: CLI, question: str) -> None:
    ids = ids_for_reads(cli)
    hits = passages(cli, ids["project"], question)
    lines = [f"\n  {len(hits)} citable passage(s) for “{question}”:"]
    for h in hits:
        unit = "slide" if h["source_type"] == "ppt_file" else "page"
        lines.append(f"\n  #{h['rank']}.{h['position']} {short(h['document'])} · {unit} {h['page']}")
        if h["source_type"] == "ppt_file":
            lines.append(f"     {clip(h['title'], 100)}")
            lines.append(f"     {clip(h['body'], 130)}")
        else:
            lines += [f"     {clip(l, 130)}" for l in h["text"].splitlines()[:5] if l.strip()]
    emit("text", "\n".join(lines))


def show_conflicts(cli: CLI) -> None:
    ids = ids_for_reads(cli)
    facts = {f["id"]: fact_text(f) for f in list_facts(cli, ids["project"], echo=False)}
    cs = list_conflicts(cli, ids["project"])
    names = [ch["document_name"] for c in cs for ch in c.get("file_chunks") or [] if ch.get("document_name")]
    files = originals(cli, ids, [n for n in names if n.endswith(".pptx")], echo=False)
    lines = [f"\n  {len(cs)} conflict(s) in the pitch library:"]
    for c in cs:
        lines.append(f"\n  {c.get('category')} · {c.get('name')} {'(resolved)' if c.get('resolved') else ''}")
        lines += [f"    fact: “{clip(facts.get(i, i), 120)}”" for i in c.get("fact_ids") or []]
        for ch in c.get("file_chunks") or []:
            n = ch.get("document_name") or ""
            s = slide_of(ch.get("text") or "", files, n) if n.endswith(".pptx") else None
            lines.append(f"    {short(n)}{', slide ' + str(s) if s else ''}: “{clip(ch.get('text'), 120)}”")
    emit("text", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "verify", "conflicts", "cleanup"],
                    help="run = full demo (default); ask QUESTION = citable slides and pages for any question; "
                         "verify FILE = re-check an exported citations file; conflicts = what the detector raised; "
                         "cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="the question for `ask`, the file for `verify`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command in ("ask", "verify") and not args.arg:
        ap.error(f"{args.command} needs an argument, e.g. python3 demo.py ask \"Which case study had the lowest CPA?\" "
                 f"or python3 demo.py verify out/fernhill-cycles-citations.json")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "ask":
        banner(2, 2, "Citable slides and pages")
        ask(cli, args.arg)
        return
    if args.command == "conflicts":
        banner(2, 2, "Conflicts — approved content vs the decks")
        show_conflicts(cli)
        return
    if args.command == "verify":
        banner(2, 2, f"Verify — {args.arg}")
        verify_file(cli, args.arg)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py ask \"…\"` shows citable slides for any question; "
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

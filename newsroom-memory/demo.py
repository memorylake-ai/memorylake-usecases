#!/usr/bin/env python3
"""
Newsroom memory — a MemoryLake demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Dana Ruiz, a reporter at The Marlow Ledger (fictional), is investigating the
Harbor Street Bridge.  A public-records request comes back as three scanned
PDFs — images of paper, with no text layer to copy from.  Over the next week
she interviews the city's spokesperson, an engineer who will not be named and
a council member.

* The scans go into a project and MemoryLake reads them (OCR).  The demo
  checks there is no text layer in the files, reads MemoryLake's OCR layout
  back line by line (`proj doc inspect`), and finds a line by asking for it —
  with the page it is on.
* Each interview claim is pinned as a fact with who said it, when and how
  (`fact add` + `fact update --metadata`).  MemoryLake's contradiction
  detector checks every new fact against the records and the other claims;
  the demo shows each flag with attribution on both sides — the source and
  date of the claim, the record and page of the line it contradicts.
* Before filing, every quote of a record in the draft is checked word for word
  against the page it cites.  One misquote is planted, and caught.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RECORDS = DATA / "records"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-nws"  # memorylake-usecases / newsroom-memory
ROOT_FOLDER = f"{PREFIX}-marlow-ledger"   # the newsroom's Library folder; one sub-folder per records request

PRESIGNED = re.compile(r"https://[^\"\s]*X-Amz-[^\"\s]*")
HIDDEN = "<signed URL, not printed>"
RANGE = re.compile(r"^P(\d+)\[\d+:([\d.]+),([\d.]+),([\d.]+),([\d.]+)\]")
DETECT_WAIT = 150   # upper bound for the detector to answer the last claim (usually 10-60 s)
SETTLE = 30         # a claim counts as clean only once it has been written this long ago
WRITE_GAP = 15      # seconds between claims: back-to-back writes push detection out by minutes


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


def say(msg: str = "") -> None:
    emit("text", msg)


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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "inspect"}
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
        # Read-only calls retry on a dropped connection, and so does `fact update` (it sets the same text/metadata again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:4]) or tuple(map(str, args[:2])) == ("fact", "update")
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
            # `proj doc inspect` and Library listings carry pre-signed storage URLs: never print them.
            emit("json", PRESIGNED.sub(HIDDEN, out.rstrip()))
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out


def create(cli: CLI, args: list, refind):
    """A create call that is not idempotent: on a dropped connection, look for what it may have created before retrying."""
    for attempt in range(5):
        try:
            return cli.run(*args, echo=attempt == 0)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether it went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            found = refind()
            if found:
                return found


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


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def shown_path(p: Path) -> str:
    """The path relative to where you ran the demo (so the echoed command is the one you would type)."""
    rel = os.path.relpath(p)
    return str(p) if rel.startswith("..") else rel


def pdf_facts(path: Path) -> dict:
    """What is inside the PDF file itself: pages, embedded images, fonts.  No fonts = no text layer to copy from."""
    raw = path.read_bytes()
    return dict(pages=len(re.findall(rb"/Type\s*/Page(?!s)", raw)), images=len(re.findall(rb"/Subtype\s*/Image", raw)),
                fonts=raw.count(b"/Font"), bytes=len(raw))


def claim_meta(c: dict) -> dict:
    return dict(kind="claim", key=c["key"], source=c["source"], role=c["role"], said_on=c["said_on"], medium=c["medium"])


def attribution(meta: dict) -> str:
    return f"{meta.get('source', '?')} ({meta.get('role', '?')}), {meta.get('medium', '?')}, {meta.get('said_on', '?')}"


# --------------------------------------------------------------------------- reading

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command (each page is one call); stops only when the token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_folder(cli: CLI, parent: str, name: str, echo: bool = True):
    return next((i for i in pages(cli, ["lib", "list", parent], echo=echo)
                 if i.get("type") in ("directory", "folder") and i["name"] == name), None)


def list_documents(cli: CLI, project_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["proj", "doc", "list", "--project", project_id, "--workspace", cli.workspace, *extra], echo=echo)


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", project_id, "--workspace", cli.workspace], echo=echo)


def list_conflicts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "conflict", "list", "--project", project_id, "--workspace", cli.workspace], echo=echo)


# --------------------------------------------------------------------------- step 2: setup

def ensure_folder(cli: CLI, parent: str, name: str) -> str:
    folder = find_folder(cli, parent, name)
    if folder is None:
        folder = create(cli, ["lib", "mkdir", name, "--parent", parent, "--on-conflict", "deny"],
                        lambda: find_folder(cli, parent, name, echo=False))
        note(f"created folder {name} → {folder['item_id']}")
    else:
        note(f"folder {name} already exists → {folder['item_id']}")
    return folder["item_id"]


def ensure_project(cli: CLI, story: dict) -> str:
    spec = story["project"]
    proj = find(cli, "proj", "desk")
    if proj is None:
        proj = create(cli, ["proj", "create", "--name", spec["name"], "--custom-id", cid("desk"),
                            "--description", spec["description"], "--workspace", cli.workspace],
                      lambda: find(cli, "proj", "desk", echo=False))
        note(f"created project “{spec['name']}” → {proj['id']}")
    else:
        note(f"project “{spec['name']}” already exists → {proj['id']}")
    return proj["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created first")
        cleanup(cli, quiet=True)
    root = ensure_folder(cli, "MY_SPACE", ROOT_FOLDER)
    folder = ensure_folder(cli, root, story["records_folder"])
    ids = dict(root=root, folder=folder, project=ensure_project(cli, story))
    rr = story["records_request"]
    say(f"\n  {story['newsroom']} · {story['reporter']} · the {story['story']} story")
    say(f"    Library folder  {ROOT_FOLDER}/{story['records_folder']}/   what the records request {rr['filed']} returned")
    say(f"    project         {story['project']['name']}   records + interview claims, checked against each other")
    emit("setup", "", ids=ids, folder=f"{ROOT_FOLDER}/{story['records_folder']}", project=story["project"]["name"],
         newsroom=story["newsroom"], reporter=story["reporter"], story=story["story"], request=rr)
    return ids


# --------------------------------------------------------------------------- step 3: the records arrive (scans)

def fetch_layout(url: str) -> list[dict]:
    """MemoryLake's OCR layout for one document: one JSON line per element it read off a page.  The URL is signed: used, never printed."""
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read().decode("utf-8")
            return [json.loads(ln) for ln in body.splitlines() if ln.strip()]
        except Exception as e:  # noqa: BLE001 — network errors of every kind get the same retry
            last = e
            time.sleep(2 * (attempt + 1))
    die(f"could not read the OCR layout ({type(last).__name__})")
    return []


def read_layouts(cli: CLI, ids: dict, docs: dict[str, str], echo: bool = True) -> dict[str, list[dict]]:
    """name → OCR layout elements, via `proj doc inspect` (one call for all documents)."""
    res = cli.run("proj", "doc", "inspect", "--project", ids["project"], "--workspace", cli.workspace,
                  *docs.values(), echo=echo) or {}
    by_id = {it["document_id"]: it for it in res.get("items") or []}
    layouts = {}
    for name, doc_id in docs.items():
        it = by_id.get(doc_id)
        url = ((it or {}).get("llm_artifact") or {}).get("ocr_layout_jsonl_s3_uri")
        layouts[name] = fetch_layout(url) if url else []
    OUT.mkdir(exist_ok=True)
    (OUT / "ocr").mkdir(exist_ok=True)
    for name, els in layouts.items():
        (OUT / "ocr" / (name + ".jsonl")).write_text("".join(json.dumps(e) + "\n" for e in els), encoding="utf-8")
    return layouts


def page_lines(layout: list[dict], page: int) -> list[str]:
    """The text lines of one page, in reading order (one OCR element can hold several lines)."""
    return [ln.strip() for e in sorted(layout, key=lambda e: e.get("element_in_page", 0)) if e.get("page_num") == page
            for ln in (e.get("content") or "").splitlines() if ln.strip()]


def records(cli: CLI, ids: dict, story: dict) -> dict:
    rr = story["records_request"]
    say(f"\n  Released {rr['released']} by {rr['office']} — what is inside each file:")
    local = {}
    for r in story["records"]:
        f = pdf_facts(RECORDS / r["file"])
        local[r["file"]] = f
        say(f"    {r['file']:<32} {f['pages']} page(s) · {f['images']} page image(s) · {f['fonts']} fonts"
            + ("  → no text layer: a scan" if f["fonts"] == 0 else ""))
    existing = {i["name"]: i for i in pages(cli, ["lib", "list", ids["folder"]]) if i.get("type") == "file"}
    uploaded = 0
    for r in story["records"]:
        if r["file"] not in existing:
            existing[r["file"]] = create(cli, ["lib", "upload", shown_path(RECORDS / r["file"]), "--parent", ids["folder"],
                                               "--xattrs", json.dumps({"request": "2026-0917", "released": rr["released"]})],
                                         lambda: next((i for i in pages(cli, ["lib", "list", ids["folder"]], echo=False)
                                                       if i["name"] == r["file"]), None))
            uploaded += 1
    have = {d["name"]: d for d in list_documents(cli, ids["project"])}
    todo = [existing[r["file"]]["item_id"] for r in story["records"] if r["file"] not in have]
    if todo:
        t0 = time.time()
        # Re-importing a file the project already holds is reported as a duplicate, so a retry is safe.
        res = create(cli, ["proj", "doc", "import", "--project", ids["project"], "--workspace", cli.workspace, "--wait", *todo],
                     lambda: None)
        failed = [d for d in (res or {}).get("details") or [] if d.get("result") not in ("success", "duplicate")]
        if failed:
            die(f"{len(failed)} scan(s) failed to import")
        note(f"{uploaded} scan(s) uploaded, {len(todo)} imported and processed in {int(time.time() - t0)}s")
        have = {d["name"]: d for d in list_documents(cli, ids["project"], echo=False)}
    else:
        note(f"the project already holds all {len(story['records'])} scans")
    docs = {r["file"]: have[r["file"]]["id"] for r in story["records"]}
    say("\n  What MemoryLake read off the scans (`proj doc inspect` → its OCR layout, the signed link is used, not printed):")
    layouts = read_layouts(cli, ids, docs)
    rows, verbatim, total = [], 0, 0
    for r in story["records"]:
        lay = layouts[r["file"]]
        per_page = []
        for n, p in enumerate(r["pages"], start=1):
            got = page_lines(lay, n)
            want = [p["header"]] + [ln for ln in p["lines"] if ln.strip()]
            same = sum(1 for w in want if w in got)
            verbatim += same
            total += len(want)
            per_page.append(dict(page=n, lines=len(got), same=same, want=len(want), first=got[1:4]))
        rows.append(dict(file=r["file"], pages=per_page, kinds=sorted({e.get("type", "?") for e in lay})))
        say(f"\n    {r['file']}  ({', '.join(rows[-1]['kinds'])})")
        for pp in per_page:
            say(f"      page {pp['page']}: {pp['lines']} lines read · {pp['same']}/{pp['want']} identical to the paper")
            for ln in pp["first"]:
                say(f"        | {ln}")
    say(f"\n  {verbatim} of {total} typed lines came back word for word from {len(story['records'])} scans with no text layer")
    emit("records", "", local=local, rows=rows, verbatim=verbatim, total=total, uploaded=uploaded, imported=len(todo))
    return dict(docs=docs, layouts=layouts, verbatim=verbatim, total=total)


# --------------------------------------------------------------------------- step 4: find the line

def doc_hits(cli: CLI, ids: dict, question: str, top_k: int = 3, echo: bool = True) -> list[dict]:
    res = cli.run("search", question, "--projects", ids["project"], "--types", "document", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    return res.get("documents") or []


def chunk_views(hit: dict) -> list[dict]:
    """Every paragraph chunk of a document hit, with its page and box (fractions of the page, origin top-left)."""
    out = []
    for it in hit.get("items") or []:
        for c in (it.get("highlight") or {}).get("chunks") or []:
            m = RANGE.match(c.get("range") or "")
            out.append(dict(text=c.get("text") or "", page=int(m.group(1)) if m else None,
                            box=[round(float(x), 2) for x in m.groups()[1:]] if m else None))
    return out


def find_line(cli: CLI, ids: dict, story: dict) -> dict:
    f = story["find"]
    say(f"\n  {story['reporter']} asks: “{f['question']}”")
    hits = doc_hits(cli, ids, f["question"])
    found = None
    for h in hits:
        for c in chunk_views(h):
            lines = [ln for ln in c["text"].splitlines() if ln.strip()]
            say(f"    {h.get('rank')}. {h.get('document_name')} · page {c['page']} · box {c['box']} · {len(lines)} lines")
            for ln in lines:
                if any(t.lower() in ln.lower() for t in f["show_terms"]):
                    say(f"         | {ln}")
            if found is None and f["expect_line"] in lines:
                found = dict(file=h.get("document_name"), page=c["page"], box=c["box"], rank=h.get("rank"), line=f["expect_line"])
    if found:
        say(f"\n  the line: “{found['line']}”  — {found['file']}, page {found['page']}, rank {found['rank']}"
            + ("  ✓" if found["file"] == f["expect_file"] else f"  ✗ expected {f['expect_file']}"))
    else:
        say(f"\n  ✗ “{f['expect_line']}” is not in any returned excerpt")
    say(f"\n  Which record was the earlier inspection?  (`proj doc list --name` is a case-insensitive substring filter)")
    named = [d["name"] for d in list_documents(cli, ids["project"], "--name", f["name_filter"])]
    for n in named:
        say(f"    {n}")
    emit("find", "", question=f["question"], hits=[dict(rank=h.get("rank"), name=h.get("document_name"),
                                                         chunks=chunk_views(h)) for h in hits],
         found=found, expect_line=f["expect_line"], name_filter=f["name_filter"], named=named)
    return dict(found=bool(found and found["file"] == f["expect_file"]), page=found and found["page"], named=named)


# --------------------------------------------------------------------------- step 5: interview claims

def claim_facts(cli: CLI, ids: dict, story: dict, echo: bool = True) -> dict[str, dict]:
    """claim key → the fact holding it (matched on the exact text the demo pinned)."""
    by_text = {c["text"]: c["key"] for c in story["claims"]}
    return {by_text[f["fact"]]: f for f in list_facts(cli, ids["project"], echo=echo) if f.get("fact") in by_text}


def pin_claims(cli: CLI, ids: dict, story: dict) -> dict:
    have = claim_facts(cli, ids, story)
    written, last_write = 0, None
    for c in story["claims"]:
        if c["key"] in have:
            continue
        if last_write is not None:
            time.sleep(WRITE_GAP)
        res = cli.run("fact", "add", "--project", ids["project"], c["text"], scoped=True) or {}
        fid = res["facts"][0]["id"]
        cli.run("fact", "update", fid, "--project", ids["project"], "--metadata", json.dumps(claim_meta(c)), scoped=True)
        last_write = time.time()
        written += 1
    have = claim_facts(cli, ids, story, echo=False)
    say(f"\n  Claims on the record ({written} pinned now, {len(story['claims']) - written} from an earlier run):")
    for c in story["claims"]:
        f = have.get(c["key"])
        say(f"    • {attribution((f or {}).get('metadata') or claim_meta(c))}")
        say(f"        “{c['text']}”")
    emit("claims", "", claims=[dict(key=c["key"], text=c["text"], meta=(have.get(c["key"]) or {}).get("metadata") or claim_meta(c),
                                    fact_id=(have.get(c["key"]) or {}).get("id")) for c in story["claims"]], written=written)
    return dict(facts=have, written=written, last_write=last_write)


def expected_pairs(story: dict) -> list[tuple]:
    """('m2d', claim, files) and ('m2m', claim_a, claim_b) the story expects the detector to raise."""
    out = []
    for c in story["claims"]:
        e = c.get("expect") or {}
        if e.get("m2d"):
            out.append(("m2d", c["key"], tuple(e["m2d"].split("|"))))
        for other in e.get("m2m") or []:
            pair = tuple(sorted((c["key"], other)))
            if ("m2m",) + pair not in out:
                out.append(("m2m",) + pair)
    return out


def matches(exp: tuple, cv: dict) -> bool:
    if exp[0] == "m2d":
        return cv["category"] == "m2d" and exp[1] in cv["claims"] and any(r["file"] in exp[2] for r in cv["records"])
    return cv["category"] == "m2m" and set(exp[1:]) <= set(cv["claims"])


def wait_for_detector(cli: CLI, ids: dict, story: dict, facts: dict, layouts: dict, last_write: float | None) -> list[dict]:
    if last_write is None:
        note("no claim was written this run — reading the flags raised when they were pinned")
        return [conflict_view(c, story, facts, layouts) for c in list_conflicts(cli, ids["project"])]
    exp = expected_pairs(story)
    note(f"waiting for MemoryLake's contradiction detector (up to {DETECT_WAIT}s after the last claim)")
    emit("detector_wait", "", seconds=DETECT_WAIT)
    first = True
    while True:
        views = [conflict_view(c, story, facts, layouts) for c in list_conflicts(cli, ids["project"], echo=first)]
        first = False
        waited = time.time() - last_write
        if (all(any(matches(e, v) for v in views) for e in exp) and waited >= SETTLE) or waited >= DETECT_WAIT:
            note(f"{len(views)} flag(s) {int(waited)}s after the last claim"
                 + ("" if waited < DETECT_WAIT else " — some may still be on the way; `python3 demo.py conflicts` reads them later"))
            return views
        time.sleep(5)


# --------------------------------------------------------------------------- step 6: flags with attribution

def locate(chunk: str, layout: list[dict]) -> int | None:
    """file_chunks carry no page number: find the page whose OCR lines the excerpt contains."""
    best, score = None, 0
    for page in sorted({e.get("page_num") for e in layout}):
        n = sum(1 for ln in page_lines(layout, page) if ln in chunk)
        if n > score:
            best, score = page, n
    return best


def conflict_view(c: dict, story: dict, facts: dict, layouts: dict) -> dict:
    by_id = {f["id"]: k for k, f in facts.items()}
    claims = [by_id[i] for i in c.get("fact_ids") or [] if i in by_id]
    claim = {x["key"]: x for x in story["claims"]}
    evidence = [ln for x in story["claims"] if x["key"] in claims for ln in (x.get("evidence") or [])]
    recs = []
    for fc in c.get("file_chunks") or []:
        name = fc.get("document_name") or "?"
        text = fc.get("text") or ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        line = next((ln for ln in evidence if ln in lines), None)   # the line of the excerpt the claim is about
        recs.append(dict(file=name, page=locate(text, layouts.get(name) or []), line=line, lines=len(lines)))
    return dict(id=c["id"], category=c.get("category"), type=c.get("conflict_type"), name=c.get("name"),
                resolved=c.get("resolved"), stale=c.get("stale"), claims=claims,
                other=[s.get("fact_text") for s in c.get("fact_snapshots") or [] if s.get("fact_id") not in by_id],
                sources=[attribution((facts[k].get("metadata") or claim_meta(claim[k]))) for k in claims],
                records=recs, at=c.get("created_at"))


def show_flags(views: list[dict], story: dict) -> None:
    claim = {x["key"]: x for x in story["claims"]}
    for v in sorted(views, key=lambda v: (v["category"] != "m2d", v["at"] or "")):
        kind = "claim vs record" if v["category"] == "m2d" else "claim vs claim"
        say(f"\n  ⚠ {v['category']} · {v['type']} · {kind} — {v['name']}")
        for k, src in zip(v["claims"], v["sources"]):
            say(f"      said by  {src}")
            say(f"               “{claim[k]['text']}”")
        for t in v["other"]:
            say(f"      also     “{clip(t, 90)}” (not one of the pinned claims)")
        for r in v["records"]:
            where = f"{r['file']}, page {r['page'] or '?'}"
            say(f"      record   {where} (released {story['records_request']['released']})")
            if r["line"]:
                say(f"               “{r['line']}”" + (f"  (+{r['lines'] - 1} more lines in the excerpt)" if r["lines"] > 1 else ""))
    if not views:
        say("\n  (no flags)")


def flags(cli: CLI, ids: dict, story: dict, pinned: dict, layouts: dict) -> dict:
    views = wait_for_detector(cli, ids, story, pinned["facts"], layouts, pinned["last_write"])
    show_flags(views, story)
    exp = expected_pairs(story)
    say("\n  Against what the reporting predicts:")
    met = 0
    for e in exp:
        ok = any(matches(e, v) for v in views)
        met += ok
        what = (f"{e[1]} ⟂ {' or '.join(e[2])}" if e[0] == "m2d" else f"{e[1]} ⟂ {e[2]}")
        say(f"    {'✓' if ok else '✗'} {e[0]}  {what}")
    flagged = {k for v in views for k in v["claims"]}
    quiet = [c["key"] for c in story["claims"] if not c.get("expect")]
    for k in quiet:
        say(f"    {'✓' if k not in flagged else '✗'} no flag  {k} (no record covers it)")
    extra = [v for v in views if not any(matches(e, v) for e in exp)]
    if extra:
        say(f"  {len(extra)} flag(s) not in the plan (shown above, not hidden)")
    paged = sum(1 for v in views for r in v["records"] if r["page"])
    recs = sum(len(v["records"]) for v in views)
    say(f"  record excerpts traced back to a page through the OCR layout: {paged}/{recs}")
    emit("flags", "", flags=views, expected=[list(e) for e in exp], met=met, quiet=quiet,
         quiet_ok=[k for k in quiet if k not in flagged], extra=len(extra), paged=paged, recs=recs)
    return dict(met=met, expected=len(exp), quiet_ok=all(k not in flagged for k in quiet), extra=len(extra),
                paged=f"{paged}/{recs}", flags=len(views))


# --------------------------------------------------------------------------- step 7: quote check

def check_quote(layouts: dict, d: dict) -> dict:
    lines = page_lines(layouts.get(d["file"]) or [], d["page"])
    ok = d["quote"] in lines
    near = None if ok else (difflib.get_close_matches(d["quote"], lines, n=1, cutoff=0.5) or [None])[0]
    return dict(quote=d["quote"], file=d["file"], page=d["page"], ok=ok, near=near, sentence=d.get("sentence", ""))


def quote_check(layouts: dict, story: dict) -> dict:
    say(f"\n  Every quote of a record in the draft, checked word for word against the page it cites:")
    rows = [check_quote(layouts, d) for d in story["draft"]]
    for r in rows:
        say(f"    {'✓' if r['ok'] else '✗'} “{r['quote']}”  — {r['file']}, page {r['page']}")
        if not r["ok"]:
            say(f"        page {r['page']} says: “{r['near']}”" if r["near"] else f"        not on page {r['page']}")
    good = sum(r["ok"] for r in rows)
    say(f"\n  {good} of {len(rows)} quotes match the scan; the misquote is caught before the story is filed")
    emit("quotes", "", rows=rows, good=good)
    return dict(good=good, total=len(rows))


# --------------------------------------------------------------------------- ask

def ask(cli: CLI, ids: dict, text: str, echo: bool = True) -> dict:
    """Facts (the claims, with who said them) and record lines (with the page), from one search."""
    story = load_story()
    facts = claim_facts(cli, ids, story, echo=False)
    by_id = {f["id"]: f for f in facts.values()}
    res = cli.run("search", text, "--projects", ids["project"], "--top-k", "8", scoped=True, echo=echo) or {}
    claims = []
    for f in res.get("facts") or []:
        meta = (by_id.get(f.get("id")) or {}).get("metadata") or {}
        claims.append(dict(text=f.get("fact") or f.get("content") or "", source=attribution(meta) if meta else "(not a pinned claim)"))
    recs = []
    for h in res.get("documents") or []:
        for c in chunk_views(h)[:2]:
            body = [ln for ln in c["text"].splitlines() if ln.strip() and not ln.startswith("PAGE ")]   # skip the release stamp
            recs.append(dict(file=h.get("document_name"), page=c["page"], text=clip(" ".join(body), 200)))
    return dict(question=text, claims=claims, records=recs)


def show_answer(r: dict) -> None:
    say(f"\n  “{r['question']}”")
    say("  claims:")
    for c in r["claims"]:
        say(f"    • “{clip(c['text'], 100)}” — {c['source']}")
    if not r["claims"]:
        say("    (none)")
    say("  records:")
    for x in r["records"]:
        say(f"    • {x['file']}, page {x['page']}: “{x['text']}”")
    if not r["records"]:
        say("    (none)")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    proj = find(cli, "proj", "desk", echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)   # the claims and their flags live on the project and go with it
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=not quiet)
    if root:
        cli.run("lib", "delete", root["item_id"])          # the records folder and every scan in it
    note("deleted the desk project (records, claims, flags) and the newsroom's Library folder")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a Library folder for the records request, one project for the story")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"The records arrive ({story['records_request']['released']}) — three scans, no text layer")
    rec = records(cli, ids, story)
    banner(4, TOTAL, "Find the line — ask a question, get the record, the page and the line")
    fl = find_line(cli, ids, story)
    banner(5, TOTAL, "Interviews — each claim pinned with who said it, when and how")
    pinned = pin_claims(cli, ids, story)
    banner(6, TOTAL, "Flags — claims that contradict the records or each other, with both sources")
    fg = flags(cli, ids, story, pinned, rec["layouts"])
    banner(7, TOTAL, "Before filing — every quote checked against the page it cites")
    qc = quote_check(rec["layouts"], story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   verbatim=f"{rec['verbatim']}/{rec['total']}", found=fl["found"], found_page=fl["page"], named=fl["named"],
                   written=pinned["written"], flags=fg["flags"], expected=f"{fg['met']}/{fg['expected']}",
                   quiet_ok=fg["quiet_ok"], extra=fg["extra"], paged=fg["paged"], quotes=f"{qc['good']}/{qc['total']}")
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=False)
    folder = root and find_folder(cli, root["item_id"], story["records_folder"], echo=False)
    proj = find(cli, "proj", "desk", echo=False)
    if not (folder and proj):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(root=root["item_id"], folder=folder["item_id"], project=proj["id"])


def current_layouts(cli: CLI, ids: dict, story: dict, echo: bool = True) -> dict:
    have = {d["name"]: d["id"] for d in list_documents(cli, ids["project"], echo=False)}
    docs = {r["file"]: have[r["file"]] for r in story["records"] if r["file"] in have}
    return read_layouts(cli, ids, docs, echo=echo) if docs else {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "conflicts", "quote", "ask", "cleanup"],
                    help="run = full demo (default); conflicts = the flags, now, with both sources; "
                         "quote \"TEXT\" --file F --page N = check a quote against a scan; ask \"TEXT\" = search claims + records; "
                         "cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="question for `ask`, quote for `quote`")
    ap.add_argument("--file", help="record the quote cites (for `quote`)")
    ap.add_argument("--page", type=int, default=1, help="page the quote cites (for `quote`, default 1)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (signed URLs are masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command in ("ask", "quote") and not args.arg:
        ap.error(f"{args.command} needs text, e.g. python3 demo.py ask \"What did the city say the repair will cost?\"")
    if args.command == "quote" and not args.file:
        ap.error("quote needs --file, e.g. --file inspection-2026-03-14.pdf --page 2")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py conflicts`, `python3 demo.py ask \"What did the city say the repair will cost?\"` "
              "or `python3 demo.py quote \"Repair estimate: 2.4 million dollars.\" --file inspection-2026-03-14.pdf --page 2`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    story = load_story()
    ids = need_ids(cli)
    if args.command == "conflicts":
        banner(2, 2, "Flags — claims that contradict the records or each other, with both sources")
        facts = claim_facts(cli, ids, story)
        layouts = current_layouts(cli, ids, story, echo=False)
        show_flags([conflict_view(c, story, facts, layouts) for c in list_conflicts(cli, ids["project"])], story)
    elif args.command == "quote":
        banner(2, 2, f"Quote check — {args.file}, page {args.page}")
        r = check_quote(current_layouts(cli, ids, story), dict(quote=args.arg, file=args.file, page=args.page))
        say(f"\n  {'✓' if r['ok'] else '✗'} “{r['quote']}”  — {r['file']}, page {r['page']}")
        if not r["ok"]:
            say(f"    page {r['page']} says: “{r['near']}”" if r["near"] else f"    not on page {r['page']}")
    elif args.command == "ask":
        banner(2, 2, "Ask — claims (with who said them) and records (with the page)")
        show_answer(ask(cli, ids, args.arg))


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

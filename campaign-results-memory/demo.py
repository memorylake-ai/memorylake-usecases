#!/usr/bin/env python3
"""
Campaign memory for marketing teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Outdoor (fictional) keeps one MemoryLake project as its campaign
memory: the exports from every tool it runs campaigns in, the results the team
wrote down in each retro, and a "what worked" reflection built from them.
Today, October 14, Dana Whitlow plans the Q1 2027 launch from that memory.

Every remembered number is true: each one equals a cell in its tool's export.
But the email platform counts orders it credits within 5 days of a click, per
unique click; the ads manager counts purchases within 7 days of a click or 1
day of a view, per link click; the search console counts fractional
data-driven conversions, per click; and the creator's 12% comes from the
creator's own recap. The reflection ranks them side by side, and the brief
says: lead with creators and email, trim search.

Step 5 finds each number's yardstick in the export it came from: the column
whose cell equals the remembered number, read with `search --types document`
(an .xlsx hit is a whole table, one `Column: value` line per row, cited by
cells). Seven numbers, four yardsticks, one of them unknown. Step 6 re-reads
every campaign from the one export that measures them all the same way (site
analytics: orders per landing session, last non-direct click). The ranking
turns over: search first, creators and paid social last. The results are
written back on that yardstick, and the reflection is rewritten in place.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
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
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = DATA / "sources"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-crs"  # memorylake-usecases / campaign-results-memory


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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search", "trace"}
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
        # Read-only calls retry on a dropped connection, and so does `fact update` (it sets the same text/metadata again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("fact", "update")
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
        # `ws list` is newest first: its first item is whichever workspace was created last. Take the team's
        # default workspace, so `run` and `cleanup` land in the same one even after you add another.
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
    return cli



# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def lib_name(file: str) -> str:
    """The Library file name: prefixed, so copies of the demo with another PREFIX never overwrite
    (`--on-conflict overwrite`) or delete (cleanup by name) each other's files."""
    return f"{PREFIX}-{file}"


def file_of(name: str) -> str:
    return name[len(PREFIX) + 1:] if name and name.startswith(PREFIX + "-") else name


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def pct(x) -> str:
    return "—" if x is None else f"{float(x):.1f}%"


def same_value(a, b) -> bool:
    """Cells compare as numbers when both are numbers (MemoryLake prints 3.0 as 3), else as text."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a or "").strip() == str(b or "").strip()


def result_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    return dict(key=f"crs:{r['id']}", record=r["id"], kind="result", campaign=r["campaign"], channel=r["channel"],
                by=r["by"], on=r["on"], source=r["source"])


def reflection_meta(w: dict) -> dict:
    return dict(key=f"crs:{w['id']}", record=w["id"], kind="reflection", by=w["by"], on=w["on"],
                built_from=w["built_from"])


def common_tool(story: dict) -> dict:
    return next(e for e in story["exports"] if e.get("common"))


# --------------------------------------------------------------------------- reading memory

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command: loop until the continuation token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (R1…, W1, S1…), metadata flattened in, plus the fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            k = m["record"] if m.get("kind") != "common" else "S" + m["record"][1:]
            out[k] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def list_documents(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["proj", "doc", "list", "--project", pid], size=100, echo=echo)


def list_library(cli: CLI, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


# --------------------------------------------------------------------------- the exports, read back through search

def parse_range(r: str):
    m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)$", r or "")
    return (m.group(1), int(m.group(2)), m.group(3), int(m.group(4))) if m else None


def split_row(line: str, names: list[str]) -> dict[str, str]:
    """`Campaign: Spring Trail email, Sent on: 2026-03-10, …` → {column: value}. Split only where a known column
    name follows, so a header with a comma in it (“Conversions (data-driven, fractional)”) stays whole."""
    if not names:
        return {}
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    parts = re.split(rf"(?:^|, )({alt}): ", line)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def export_rows(cli: CLI, pid: str, query: str, echo: bool = True) -> tuple[list[dict], list[str]]:
    """Table rows from `search --types document`. An .xlsx hit's `table` item carries a whole table as one chunk,
    one `Column: value` line per row, and the cells it covers (`range`, e.g. A2:F3): line i is row start+i.
    The column names (in sheet order) are in `highlight.inner_tables[].columns`."""
    res = cli.run("search", query, "--projects", pid, "--types", "document", "--top-k", "5",
                  scoped=True, echo=echo) or {}
    rows, problems = [], []
    for d in res.get("documents") or []:
        for it in d.get("items") or []:
            if it.get("type") != "table":
                continue
            hl = it.get("highlight") or {}
            names = [col.get("name") for t in (hl.get("inner_tables") or it.get("inner_tables") or [])
                     for col in t.get("columns") or [] if col.get("name") and col.get("name") != "excel_row_number"]
            for ch in hl.get("chunks") or []:
                rg = parse_range(ch.get("range") or "")
                lines = (ch.get("text") or "").split("\n")
                if not rg or rg[3] - rg[1] + 1 != len(lines):
                    problems.append(f"{file_of(d.get('document_name'))} {ch.get('range')}: {len(lines)} line(s) for that range — not cited")
                    continue
                for i, line in enumerate(lines):
                    r = rg[1] + i
                    rows.append(dict(document=file_of(d.get("document_name") or ""), sheet=d.get("sheet_name"),
                                     row=r, cells=f"{rg[0]}{r}:{rg[2]}{r}", columns=names, values=split_row(line, names)))
    return rows, problems


def cite(row: dict) -> str:
    return f"{row['document']} · {row['sheet']}!{row['cells']}"


def read_exports(cli: CLI, pid: str, story: dict, echo: bool = True) -> tuple[list[dict], list[str]]:
    """Every row of every export, deduplicated; asked until all four tables are back (top 5 documents)."""
    rows, problems = export_rows(cli, pid, "campaign conversion rate orders clicks sessions", echo=echo)
    seen, out = set(), []
    for r in rows:
        k = (r["document"], r["row"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    have = {r["document"] for r in out}
    missing = [e["file"] for e in story["exports"] if e["file"] not in have]
    for f in missing:
        more, p2 = export_rows(cli, pid, f"{f.replace('-', ' ').replace('.xlsx', '')} campaign", echo=False)
        problems += p2
        for r in more:
            k = (r["document"], r["row"])
            if k not in seen:
                seen.add(k)
                out.append(r)
    return out, problems


def rate_column(names: list[str]) -> str | None:
    """The export's conversion rate: the column whose header reads `X / Y (%)`."""
    for n in names:
        if re.search(r"/.+\(%\)\s*$", n):
            return n
    return None


def yardstick(header: str, columns: list[str]) -> dict:
    """What the number counts and what it divides by, read off the export's own headers:
    `Orders / unique clicks (%)` → numerator column (the one counting orders), denominator `unique clicks`."""
    m = re.match(r"\s*(.+?)\s*/\s*(.+?)\s*\(%\)\s*$", header)
    num_word, den = (m.group(1), m.group(2)) if m else (header, "?")
    numerator = next((c for c in columns if c != header and c.lower().startswith(num_word.lower())), num_word)
    return dict(header=header, numerator=numerator, denominator=den, label=f"{numerator} / {den}")


def column_value(row: dict, name: str):
    """A cell by column name, ignoring case (headers say “Landing sessions”, the yardstick says “landing sessions”)."""
    for k, v in row["values"].items():
        if k.lower() == (name or "").lower():
            return v
    return None


# --------------------------------------------------------------------------- step 2: setup

def upload_and_import(cli: CLI, pid: str, files: list[tuple[str, Path]]) -> None:
    ids = []
    for i, (name, path) in enumerate(files):
        item = cli.run("lib", "upload", os.path.relpath(path), "--name", name, "--on-conflict", "overwrite", echo=i == 0)
        ids.append(item["item_id"])
    if len(files) > 1:
        note(f"… and {len(files) - 1} more `lib upload` the same way")
    t0 = time.time()
    res = cli.run("proj", "doc", "import", "--project", pid, *ids, "--wait", scoped=True) or {}
    note(f"imported {res.get('success_count', len(ids))} export(s) in {int(time.time() - t0)} s")


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    p = story["project"]
    proj = find_project(cli, p["key"], echo=False)
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                       "--description", p["description"], scoped=True)
    else:
        note("the campaign memory exists from an earlier run (pass --reset to start over)")
    pid = proj["id"]
    have = {d["name"]: d for d in list_documents(cli, pid, echo=False)}
    todo = [(lib_name(e["file"]), SOURCES / e["file"]) for e in story["exports"] if lib_name(e["file"]) not in have]
    if todo:
        note(f"{len(todo)} export(s) arrive, one per tool: {', '.join(file_of(n) for n, _ in todo)}")
        upload_and_import(cli, pid, todo)
    docs = {d["name"]: d for d in list_documents(cli, pid)}
    lines = [f"\n    project  {p['name']:<52} {pid}"]
    for e in story["exports"]:
        d = docs.get(lib_name(e["file"])) or {}
        lines.append(f"    export   {e['file']:<28} {e['tool']:<26} {d.get('status', 'missing'):<7} {d.get('id', '')}")
    emit("text", "\n".join(lines))
    ids = dict(project=pid)
    emit("setup", "", project=pid, exports=[dict(e, status=(docs.get(lib_name(e["file"])) or {}).get("status"))
                                            for e in story["exports"]])
    return ids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], live: dict, echo: bool = True) -> int:
    """Pin the entries the project does not hold yet (matched by metadata key): one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns how many were new."""
    missing = [(t, m) for t, m in items if m["key"] not in live]
    if not missing:
        return 0
    res = cli.run("fact", "add", "--project", pid, *[t for t, _ in missing], scoped=True, echo=echo) or {}
    got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
    for i, (text, meta) in enumerate(missing):
        fid = got.get(text)
        if not fid:
            die(f"`fact add` did not return “{clip(text, 60)}”")
        cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta, ensure_ascii=False), scoped=True, echo=echo and i == 0)
    if len(missing) > 1 and echo:
        note(f"… and `fact update --metadata` on the other {len(missing) - 1}")
    return len(missing)


def restore_planning_day(cli: CLI, pid: str, story: dict) -> int:
    """Every run starts on planning day: what an earlier run wrote back in step 6 is undone first —
    the results on one yardstick are deleted, the reflection and the results get their original text and labels."""
    w = story["reflection"]
    by_id = {r["id"]: r for r in story["results"]}
    undone = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("kind") == "common":
            cli.run("fact", "delete", "--project", pid, f["id"], scoped=True, echo=undone == 0)
            undone += 1
        elif m.get("kind") == "reflection" and (m.get("previous") or f.get("fact") != w["text"]):
            cli.run("fact", "update", f["id"], "--project", pid, "--text", w["text"],
                    "--metadata", json.dumps(reflection_meta(w), ensure_ascii=False), scoped=True, echo=undone == 0)
            undone += 1
        elif m.get("kind") == "result" and m.get("yardstick") and m.get("record") in by_id:
            cli.run("fact", "update", f["id"], "--project", pid, "--metadata", json.dumps(result_meta(by_id[m["record"]]), ensure_ascii=False),
                    scoped=True, echo=undone == 0)
            undone += 1
    if undone:
        note(f"an earlier run wrote back {undone} change(s); undone, so this run starts from the same memory")
    return undone


def intake(cli: CLI, ids: dict, story: dict) -> dict:
    pid = ids["project"]
    restored = restore_planning_day(cli, pid, story)
    note("what the team wrote down after each campaign, word for word (one `fact add`), then labelled with "
         "`fact update --metadata`: campaign, channel, who, when, and which report the number came from")
    live = {}
    for f in list_facts(cli, pid, echo=False):
        k = (f.get("metadata") or {}).get("key")
        if k:
            live[k] = f
    items = [(r["text"], result_meta(r)) for r in story["results"]]
    w = story["reflection"]
    items.append((w["text"], reflection_meta(w)))
    new = pin(cli, pid, items, live)
    if not new:
        note("the retro notes were pinned by an earlier run; reading them back")
    else:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new} new fact(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    lines = [f"\n  {story['brand']} campaign memory — {len(mem)} records:"]
    for k in sorted(mem, key=lambda k: (k[0] != "R", k)):
        r = mem[k]
        who = f"{r['by']}, {r['on']}"
        lines.append(f"    {k:<3} {who:<24} {clip(r['body'], 96)}")
    lines.append(f"\n  {w['id']} is the reflection memory — “what worked” — built from {', '.join(w['built_from'])}.")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[dict(record=k, **{x: mem[k].get(x) for x in ("kind", "campaign", "channel", "by", "on",
                                                                              "source", "body", "fid")})
                                for k in sorted(mem, key=lambda k: (k[0] != "R", k))], restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: plan the way memory says

RATE = re.compile(r"(\d+(?:\.\d+)?)%\s+conversion")


def search_facts(cli: CLI, pid: str, q: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        rec = m.get("record") or "?"
        if m.get("kind") == "common":
            rec = "S" + rec[1:]
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "", record=rec))
    return out


def channel_ranking(rates: dict[str, list[float]]) -> list[tuple[str, float]]:
    return sorted(((ch, sum(v) / len(v)) for ch, v in rates.items() if v), key=lambda x: -x[1])


def brief_line(ranking: list[tuple[str, float]]) -> str:
    if len(ranking) < 2:
        return "not enough results to rank channels"
    lead = [ch for ch, _ in ranking[:2]]
    return f"lead with {lead[0]} and {lead[1]}, trim {ranking[-1][0]}"


def hit_line(h: dict) -> str:
    return f"      #{h['rank']:<3} {h['record']:<3} {clip(h['body'], 104)}"


def plan(cli: CLI, ids: dict, story: dict, mem: dict) -> dict:
    pid = ids["project"]
    q = story["questions"]["plan"]
    note(f"{story['planner']['name']} drafts {story['planner']['task']} from campaign memory: one search, top 8 facts")
    hits = search_facts(cli, pid, q)
    lines = [f"\n  “{q}” · top {len(hits)}:"] + [hit_line(h) for h in hits]
    # `search` caches by query text: a repeat of the same question can come back with an earlier run's facts.
    live = {r["fid"]: r["body"] for r in mem.values()}
    cached = sorted({h["record"] for h in hits if live.get(h["fid"]) != h["body"]})
    if cached:
        note(f"{len(cached)} hit(s) differ from what `fact list` holds now ({', '.join(cached)}) — search answered from its cache")
    # Read the way a person (or an assistant) reads the hits: the number in the sentence, by channel.
    read, rates = [], {}
    for h in hits:
        m = RATE.search(h["body"])
        if h.get("kind") == "result" and m:
            v = float(m.group(1))
            read.append(dict(record=h["record"], campaign=h.get("campaign"), channel=h.get("channel"), rate=v))
            rates.setdefault(h.get("channel"), []).append(v)
    read.sort(key=lambda r: -r["rate"])
    ranking = channel_ranking(rates)
    lines.append(f"\n  the numbers in those hits, highest first ({len(read)} results):")
    for i, r in enumerate(read, 1):
        lines.append(f"      {i}. {r['record']}  {r['campaign']:<28} {r['channel']:<12} {pct(r['rate'])}")
    lines.append("  by channel: " + " · ".join(f"{ch} {v:.1f}%" for ch, v in ranking))
    refl = next((h for h in hits if h.get("kind") == "reflection"), None)
    if refl:
        lines.append(f"  the reflection memory agrees ({refl['record']}, #{refl['rank']}): “{clip(refl['body'], 120)}”")
    brief = brief_line(ranking)
    lines.append(f"\n  → the Q1 brief, as memory has it: {brief.upper()}")
    emit("text", "\n".join(lines))
    out = dict(q=q, hits=hits, read=read, ranking=ranking, brief=brief, cached=cached,
               reflection=dict(record=refl["record"], rank=refl["rank"], body=refl["body"]) if refl else None)
    emit("plan", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: the yardstick check

def yardstick_check(cli: CLI, ids: dict, story: dict, mem: dict | None = None, echo: bool = True) -> dict:
    """For every remembered result: find its row in the export it names, and the cell that equals the remembered
    number. That column's header is the number's yardstick. A number with no such cell has no known yardstick."""
    pid = ids["project"]
    mem = mem or entries(list_facts(cli, pid, echo=False))
    note("every remembered number, back to the export it came from: `search --types document` returns each .xlsx as a "
         "table (one `Column: value` line per row, cited by cells). The yardstick is the header of the column whose cell "
         "equals the remembered number")
    rows, problems = read_exports(cli, pid, story, echo=echo)
    for p in problems:
        note(p)
    results = sorted((r for r in mem.values() if r.get("kind") == "result"), key=lambda r: r["record"])
    out, lines = [], [f"\n    {'id':<3} {'campaign':<28} {'remembered':>10}  {'yardstick (from the export headers)':<56} cited"]
    for r in results:
        m = RATE.search(r["body"])
        remembered = float(m.group(1)) if m else None
        cands = [x for x in rows if x["document"] == r["source"] and x["values"].get("Campaign") == r["campaign"]]
        hit = None
        for x in cands:
            for col in x["columns"]:
                if col != "Campaign" and remembered is not None and same_value(x["values"].get(col), remembered):
                    hit = (x, col)
                    break
            if hit:
                break
        if hit:
            x, col = hit
            ys = yardstick(col, x["columns"])
            item = dict(record=r["record"], campaign=r["campaign"], channel=r["channel"], remembered=remembered,
                        found=True, cite=cite(x), document=x["document"], cells=x["cells"], cell=x["values"].get(col),
                        numerator_value=x["values"].get(ys["numerator"]), **ys)
            lines.append(f"    {r['record']:<3} {r['campaign']:<28} {pct(remembered):>10}  {clip(ys['label'], 56):<56} {item['cite']}")
        else:
            why = "no such export in the project" if not any(x["document"] == r["source"] for x in rows) else \
                  "no cell in its row equals the number"
            item = dict(record=r["record"], campaign=r["campaign"], channel=r["channel"], remembered=remembered,
                        found=False, header=None, label="unknown", why=why, source=r["source"])
            lines.append(f"    {r['record']:<3} {r['campaign']:<28} {pct(remembered):>10}  {'✗ unknown — ' + why:<56} source: {clip(r['source'], 40)}")
        out.append(item)
    sticks = []
    for it in out:
        if it["label"] not in sticks:
            sticks.append(it["label"])
    found = [it for it in out if it["found"]]
    lines.append(f"\n  → {len(found)} of {len(out)} numbers equal a cell in their export, word for word — every one of them is true.")
    lines.append(f"  → and they are measured {len(sticks)} different ways:")
    for s in sticks:
        who = [it["record"] for it in out if it["label"] == s]
        lines.append(f"      {', '.join(who):<8} {s}")
    lines.append("  → the step-4 ranking put all of them on one scale. "
                 + ("It is not comparing channels; it is comparing yardsticks." if len(sticks) > 1 else ""))
    emit("text", "\n".join(lines))
    res = dict(rows=out, yardsticks=sticks, problems=problems, export_rows=len(rows))
    emit("yardsticks", "", **res)
    return res


# --------------------------------------------------------------------------- step 6: one yardstick

def common_text(r: dict, rate: float, tool: dict, ys: dict) -> str:
    return (f"{r['campaign']} ({r['channel']}): {rate:.1f}% on the common yardstick, {tool['tool']}: "
            f"{ys['numerator'].lower()} / {ys['denominator']}.")


def reflection_text(ranking: list[tuple[str, float]], by_ch: dict[str, list[float]], ys: dict, sticks: int) -> str:
    parts = [f"{ch} ({', '.join(f'{v:.1f}%' for v in sorted(by_ch[ch], reverse=True))})" for ch, _ in ranking]
    return (f"What worked in 2026, every campaign measured the same way ({ys['numerator'].lower()} / "
            f"{ys['denominator']}): {', then '.join(parts)}. The earlier ranking compared {sticks} different yardsticks. "
            f"For the Q1 2027 launch: lead with {ranking[0][0]}, keep {ranking[1][0]}, "
            f"and judge {ranking[-1][0]} on this yardstick before spending more.")


def one_yardstick(cli: CLI, ids: dict, story: dict, mem: dict, pl: dict, yc: dict) -> dict:
    pid = ids["project"]
    tool = common_tool(story)
    note(f"one export measures every campaign the same way: {tool['file']} ({tool['tool']}). Its rows, read the same "
         "way, cited by cells")
    rows, _ = read_exports(cli, pid, story, echo=True)
    site = {x["values"].get("Campaign"): x for x in rows if x["document"] == tool["file"]}
    cols = next((x["columns"] for x in site.values()), [])
    rc = rate_column(cols)
    ys = yardstick(rc, cols) if rc else dict(header="?", numerator="?", denominator="?", label="?")
    results = sorted((r for r in mem.values() if r.get("kind") == "result"), key=lambda r: r["record"])
    before = {r["record"]: i for i, r in enumerate(pl["read"], 1)}
    by_ch, table = {}, []
    for r in results:
        x = site.get(r["campaign"])
        v = float(x["values"][rc]) if x and rc and x["values"].get(rc) not in (None, "") else None
        rem = next((y for y in yc["rows"] if y["record"] == r["record"]), {})
        table.append(dict(record=r["record"], campaign=r["campaign"], channel=r["channel"], remembered=rem.get("remembered"),
                          was=rem.get("label"), common=v, cite=cite(x) if x else None,
                          sessions=column_value(x, ys["denominator"]) if x else None,
                          orders=x["values"].get(ys["numerator"]) if x else None))
        if v is not None:
            by_ch.setdefault(r["channel"], []).append(v)
    table.sort(key=lambda t: -(t["common"] if t["common"] is not None else -1))
    for i, t in enumerate(table, 1):
        t["after"], t["before"] = i, before.get(t["record"])
    ranking = channel_ranking(by_ch)
    lines = [f"\n  yardstick: {ys['label']}  ({tool['file']})",
             f"\n    {'now':<4} {'was':<4} {'id':<3} {'campaign':<28} {'remembered':>10} {'one yardstick':>14}   cited"]
    for t in table:
        lines.append(f"    {t['after']:<4} {str(t['before'] or '—'):<4} {t['record']:<3} {t['campaign']:<28} {pct(t['remembered']):>10} "
                     f"{pct(t['common']):>14}   {t['cite'] or '— no row'}")
    lines.append("\n  by channel, before → after:")
    old = dict(pl["ranking"])
    for ch, v in ranking:
        lines.append(f"      {ch:<12} {pct(old.get(ch)):>7} → {pct(v)}")
    brief = brief_line(ranking)
    lines.append(f"  → the Q1 brief on one yardstick: {brief.upper()}   (was: {pl['brief']})")
    emit("text", "\n".join(lines))
    emit("common", "", yardstick=ys, file=tool["file"], table=table, ranking=ranking, before=pl["ranking"], brief=brief,
         was=pl["brief"])

    # Write it back, so the next planner reads the corrected memory, not this run's output.
    note("written back to memory: each result gets its yardstick as a label (`fact update --metadata`); each campaign gets "
         "its number on the common yardstick (`fact add`); the reflection is rewritten in place (`fact update --text`), "
         "its old text kept in metadata")
    live = {}
    facts = list_facts(cli, pid, echo=False)
    for f in facts:
        k = (f.get("metadata") or {}).get("key")
        if k:
            live[k] = f
    first = True
    for y in yc["rows"]:
        r = mem.get(y["record"])
        if not r:
            continue
        meta = dict(result_meta(next(s for s in story["results"] if s["id"] == y["record"])),
                    yardstick=y["label"] if y["found"] else "unknown")
        if y["found"]:
            meta["yardstick_cite"] = y["cite"]
        cli.run("fact", "update", r["fid"], "--project", pid, "--metadata", json.dumps(meta, ensure_ascii=False), scoped=True, echo=first)
        first = False
    by_id = {r["id"]: r for r in story["results"]}
    items = []
    for t in table:
        if t["common"] is None:
            continue
        items.append((common_text(by_id[t["record"]], t["common"], tool, ys),
                      dict(key=f"crs:{t['record']}:common", record=t["record"], kind="common", campaign=t["campaign"],
                           channel=t["channel"], yardstick=ys["label"], cite=t["cite"], on=story["today"])))
    pin(cli, pid, items, live)
    w = story["reflection"]
    wf = mem.get(w["id"])
    new_text = reflection_text(ranking, by_ch, ys, len(yc["yardsticks"]))
    if wf:
        meta = dict(reflection_meta(w), previous=w["text"], revised_on=story["today"], yardstick=ys["label"])
        cli.run("fact", "update", wf["fid"], "--project", pid, "--text", new_text, "--metadata", json.dumps(meta, ensure_ascii=False), scoped=True)
    # A new question, not this morning's: `search` answers a repeated question from its cache.
    q = story["questions"]["after"]
    note("planning again, new wording (search answers a repeated question from its cache), until the rewritten "
         "reflection is in the search index")
    t1, tries = time.time(), 0
    while True:
        tries += 1
        hits = search_facts(cli, pid, q, echo=tries == 1)
        fresh = any(h.get("kind") == "reflection" and h["body"] == new_text for h in hits)
        if fresh or tries >= 8:
            break
        time.sleep(4)
    if tries > 1:
        note(f"the search index caught up on try {tries} ({int(time.time() - t1)} s)" if fresh
             else f"the rewritten reflection was not in the top {len(hits)} after {tries} tries ({int(time.time() - t1)} s)")
    lines = [f"\n  “{q}” · top {len(hits)}:"] + [hit_line(h) for h in hits]
    # The labels written back are what make the read safe: only numbers on the same yardstick are compared.
    same = [h for h in hits if h.get("kind") == "common" and h.get("yardstick") == ys["label"]]
    other = [h for h in hits if h.get("kind") == "result"]
    lines.append(f"\n  comparable (labelled “{ys['label']}”): {', '.join(h['record'] for h in same) or 'none'}")
    if other:
        lines.append(f"  not compared (labelled with another yardstick): "
                     + ", ".join(f"{h['record']} ({clip(h.get('yardstick') or 'unlabelled', 40)})" for h in other))
    refl = next((h for h in hits if h.get("kind") == "reflection"), None)
    if refl:
        lines.append(f"  the reflection memory now (#{refl['rank']}, same fact id, rewritten): “{clip(refl['body'], 150)}”")
    # Search's top 8 is a hint; the ranking counts every number on that yardstick (`fact list`, every page).
    rates = {}
    for f in list_facts(cli, pid):
        m = f.get("metadata") or {}
        if m.get("kind") == "common" and m.get("yardstick") == ys["label"]:
            v = re.search(r"(\d+(?:\.\d+)?)% on the common yardstick", f.get("fact") or "")
            if v:
                rates.setdefault(m.get("channel"), []).append(float(v.group(1)))
    after_rank = channel_ranking(rates)
    lines.append(f"  → every number on that yardstick (`fact list`): {' · '.join(f'{ch} {v:.1f}%' for ch, v in after_rank)}")
    lines.append(f"  → the Q1 brief: {brief_line(after_rank).upper()}")
    emit("text", "\n".join(lines))
    out = dict(yardstick=ys, file=tool["file"], table=table, ranking=ranking, brief=brief, was=pl["brief"], q=q,
               hits=hits, tries=tries, fresh=fresh, after_rank=after_rank, after_brief=brief_line(after_rank),
               reflection=new_text)
    emit("planned", "", **{k: out[k] for k in ("q", "hits", "tries", "fresh", "after_rank", "after_brief", "reflection")})
    return out


# --------------------------------------------------------------------------- read-only: ask

def ask(cli: CLI, ids: dict, q: str, echo: bool = True) -> dict:
    hits = search_facts(cli, ids["project"], q, echo=echo)
    return dict(q=q, hits=hits)


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"])


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    gone = 0
    if proj:
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        for f in facts:
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted the project, its {len(facts)} fact(s) and its documents")
        gone += 1
    names = {lib_name(e["file"]) for e in story["exports"]}
    removed = 0
    for item in list_library(cli, echo=not quiet):
        if item.get("name") in names:
            cli.run("lib", "delete", item.get("item_id") or item.get("id"), echo=not quiet and removed == 0)
            removed += 1
    note(f"deleted {removed} uploaded export(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project for campaign memory, one export per tool")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"Intake — {len(story['results'])} results from the retros and the “what worked” reflection")
    mem = intake(cli, ids, story)
    banner(4, TOTAL, "Plan the Q1 launch the way memory says")
    pl = plan(cli, ids, story, mem)
    banner(5, TOTAL, "The yardstick check — every number, back to the export it came from")
    yc = yardstick_check(cli, ids, story, mem)
    banner(6, TOTAL, "One yardstick — re-rank, write it back, plan again")
    oy = one_yardstick(cli, ids, story, mem, pl, yc)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   plan_hits=[h["record"] for h in pl["hits"]], cached=pl["cached"], before=pl["ranking"], brief=pl["brief"],
                   found=sum(1 for r in yc["rows"] if r["found"]), yardsticks=len(yc["yardsticks"]),
                   after=oy["ranking"], after_brief=oy["brief"], replan=oy["after_brief"], replan_tries=oy["tries"],
                   replan_fresh=oy["fresh"], replan_hits=[h["record"] for h in oy["hits"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "yardsticks", "ask", "cleanup"],
                    help="run = full demo (default); yardsticks = the yardstick check of every remembered result; "
                         "ask \"QUESTION\" = search's top 8 facts in the campaign memory; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"Which channel converts best?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py yardsticks` or `python3 demo.py ask \"Which channel converts best?\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "yardsticks":
        banner(2, 2, "The yardstick check — every remembered number, back to its export")
        yardstick_check(cli, ids, story)
    elif a.command == "ask":
        banner(2, 2, "Ask — top 8 facts in the campaign memory")
        r = ask(cli, ids, a.args[0])
        lines = [f"\n  “{r['q']}” — top {len(r['hits'])}:"] + [hit_line(h) for h in r["hits"]]
        emit("text", "\n".join(lines))


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

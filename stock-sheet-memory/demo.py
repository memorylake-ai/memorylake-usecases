#!/usr/bin/env python3
"""stock-sheet-memory — a weekly stock count behind MemoryRouter, and the warehouse name that is only a tab.

Harrow & Pike Distribution (a fictional distributor) counts stock in four warehouses every Monday. The count is one
.xlsx workbook with a sheet per warehouse — Leeds, Bristol, Glasgow, Cardiff — and the same columns on every sheet.
The operations team keeps it in a MemoryLake project, and the stock assistant is an ordinary model call through
MemoryRouter with `--boundary`: MemoryRouter attaches what it finds relevant, documents included.

The index knows which sheet every table came from: each table item in `search` carries its own sheet_name. The
search hit for the file carries one more, file-level sheet_name. MemoryRouter attaches each table as an entry
labelled "(file / sheet)" — and the sheet in that label is one name for the whole file, often not the table's own:
Bristol's rows arrive as "(stock-count.xlsx / Glasgow) SKU: …, On hand: 512". The rows themselves never say which
warehouse. The model answers Glasgow's question with Bristol's number; with a label that says Glasgow, it has no
way to know better.

The demo shows:
  · the count in the index (`search --types document`): one table per sheet, each with the right sheet name;
  · eight questions the team asks every week, through a boundary over the workbook as kept, and through one over
    the same numbers with a Site column on every row; each answered several times, graded against the count:
    right, the same item at another warehouse, or no figure; a checker call (Claude copies out what it was given)
    shows every attached table's label next to the warehouse its numbers belong to;
  · where every wrong number came from: which warehouse it belongs to, and under which label its table was
    attached.

What MemoryRouter attaches varies from call to call; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (80 calls per run, roughly 35,000 tokens).
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

PREFIX = "mlu-stk"  # memorylake-usecases / stock-sheet-memory


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
ARMS = (("tabs", "the workbook as kept (warehouse = sheet name)"), ("sited", "Site column on every row"))
SHORT = {"tabs": "as kept", "sited": "Site column"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "stock.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(file: str) -> str:
    return f"{PREFIX}-{file}"


def arm_file(d: dict, arm: str) -> str:
    return d["file"] if arm == "tabs" else d["file_sited"]


def site_of(d: dict, name: str) -> dict:
    for s in d["sites"]:
        if s["site"].lower() == name.strip().lower():
            return s
    die(f"no warehouse {name!r}; one of: {', '.join(s['site'] for s in d['sites'])}")


def item_of(d: dict, sku: str) -> dict:
    return next(i for i in d["items"] if i["sku"] == sku)


# --------------------------------------------------------------------------- grading

NUM = re.compile(r"(?<![A-Za-z0-9.,-])(\d{1,3}(?:,\d{3})+|\d+)(?![A-Za-z0-9])")


def reply_numbers(text: str) -> set[int]:
    """Whole numbers in a reply (1,430 and 1430 alike). SKU codes (BR-6204) and item names (M10x40) are skipped."""
    return {int(m.group(1).replace(",", "")) for m in NUM.finditer(text or "")}


def grade(d: dict, q: dict, reply: str) -> dict:
    """right = the asked warehouse's count and no other warehouse's; other = another warehouse's count of the same
    item (named); mixed = both; none = neither (no figure, or "not given")."""
    nums = reply_numbers(reply)
    truth = site_of(d, q["site"])["on_hand"][q["sku"]]
    others = [s["site"] for s in d["sites"] if s["site"] != q["site"] and s["on_hand"][q["sku"]] in nums]
    if truth in nums:
        return dict(verdict="mixed" if others else "right", from_sites=others)
    return dict(verdict="other" if others else "none", from_sites=others)


MARK = {"right": "✓", "other": "✗", "mixed": "±", "none": "·", None: "–"}


def check_numbers(d: dict) -> None:
    """Every count in the data is distinct, and none equals a number printed elsewhere in a row or a question —
    so a number in a reply points at exactly one warehouse."""
    counts = [v for s in d["sites"] for v in s["on_hand"].values()]
    other = {i["reorder"] for i in d["items"]}
    for text in [i["sku"] + " " + i["item"] for i in d["items"]] + [q["say"] for q in d["questions"]]:
        other |= {int(x) for x in re.findall(r"\d+", text)}
    if len(set(counts)) != len(counts) or set(counts) & other:
        die("data/stock.json: every count must be distinct and differ from SKU numbers and reorder points")


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
    if demo_boundaries(cli) or find_project(cli, "tabs") or find_project(cli, "sited") or demo_files(cli):
        note("an earlier run left the team's memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    a = cli.run("proj", "create", "--name", f"{d['company']} — stock count (as kept)", "--custom-id", cid("tabs"),
                "--description", "The Monday stock count, one sheet per warehouse. Demo data.", scoped=True)
    b = cli.run("proj", "create", "--name", f"{d['company']} — stock count (Site column)", "--custom-id", cid("sited"),
                "--description", "The same count with a Site column on every row. Demo data.", scoped=True)
    ba = cli.run("boundary", "create", "--name", "Stock assistant: count as kept", "--custom-id", cid("tabs"),
                 "--projects", a["id"], scoped=True)
    bb = cli.run("boundary", "create", "--name", "Stock assistant: count with Site column", "--custom-id", cid("sited"),
                 "--projects", b["id"], scoped=True)
    note(f"boundary {ba['id']} covers the workbook as the team keeps it; boundary {bb['id']} covers the same numbers "
         "with a Site column. Every answer is `router chat … --boundary` and MemoryRouter picks what to attach")
    world = dict(tabs=a["id"], sited=b["id"], boundary={"tabs": ba["id"], "sited": bb["id"]})
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    a, b = find_project(cli, "tabs"), find_project(cli, "sited")
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    if not a or not b or cid("tabs") not in bs or cid("sited") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(tabs=a["id"], sited=b["id"], boundary={"tabs": bs[cid("tabs")], "sited": bs[cid("sited")]})


# --------------------------------------------------------------------------- step 3: the count

def import_file(cli: CLI, project: str, path: Path) -> None:
    item = cli.run("lib", "upload", os.path.relpath(path), "--name", lib_name(path.name), "--on-conflict", "overwrite")["item_id"]
    t0 = time.time()
    try:
        cli.run("proj", "doc", "import", "--project", project, item, "--wait", scoped=True)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the document already submitted")
    wait_docs(cli, project, 1)
    note(f"{lib_name(path.name)} uploaded and indexed in {int(time.time() - t0)} s")


def store(cli: CLI, world: dict) -> None:
    d = load_data()
    files = [DATA / d["file"], DATA / d["file_sited"]]
    missing = [p.name for p in files if not p.exists()]
    if missing:
        die(f"missing {', '.join(missing)}; run `python3 data/make_sheets.py`")
    head = "  ".join(f"{c:<22}" if c == "Item" else f"{c:<9}" for c in d["columns"])
    lines = [f"\n  {d['file']} — counted {d['counted']}, sheets: {', '.join(s['site'] for s in d['sites'])}",
             f"    sheet “{d['sites'][0]['site']}”:  {head}"]
    for i in d["items"][:3]:
        lines.append(f"    {'':<{len(d['sites'][0]['site']) + 10}}{i['sku']:<9}  {i['item']:<22}  "
                     f"{d['sites'][0]['on_hand'][i['sku']]:<9}  {i['reorder']}")
    lines.append(f"    {'':<{len(d['sites'][0]['site']) + 10}}…  every sheet has the same columns and the same six items")
    lines.append(f"  {d['file_sited']} — the same numbers; every row starts with Site (Leeds, Leeds, …)")
    emit("text", "\n".join(lines))
    import_file(cli, world["tabs"], files[0])
    import_file(cli, world["sited"], files[1])
    emit("memory", "", files=[d["file"], d["file_sited"]], sites=[s["site"] for s in d["sites"]],
         items=d["items"], columns=d["columns"])


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


# --------------------------------------------------------------------------- step 4: the count in the index

def index_tables(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every table item `search --types document` returns for the project: its sheet name and its rows."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    out, seen = [], set()
    for doc in res.get("documents") or []:
        for it in doc.get("items") or []:
            if it.get("type") != "table":
                continue
            text = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            key = (it.get("sheet_name"), text)
            if text and key not in seen:
                seen.add(key)
                out.append(dict(file=doc.get("document_name") or doc.get("file_name") or "",
                                doc_sheet=doc.get("sheet_name"), sheet=it.get("sheet_name"), rows=text.splitlines()))
    return out


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    tables = index_tables(cli, world["tabs"], "stock on hand by warehouse", echo=True)
    if not tables:
        die("search did not return the stock count yet; run again in a minute")
    lines = []
    for t in sorted(tables, key=lambda t: [s["site"] for s in d["sites"]].index(t["sheet"]) if t["sheet"] in
                    [s["site"] for s in d["sites"]] else 99):
        lines.append(f"\n  table item · sheet_name “{t['sheet']}”")
        lines += [f"    {r}" for r in t["rows"][:3]] + ([f"    … {len(t['rows']) - 3} more rows"] if len(t["rows"]) > 3 else [])
    emit("text", "\n".join(lines))
    got = sorted({t["sheet"] for t in tables if t["sheet"]})
    note(f"the index holds one table per sheet and each table item knows its sheet ({', '.join(got)})")
    doc_sheet = tables[0]["doc_sheet"]
    emit("text", f"\n  the search hit itself (the file) carries one more sheet_name: “{doc_sheet}”.\n"
                 "  MemoryRouter attaches each table as an entry labelled (file / sheet), one sheet name per file — "
                 "for example:")
    sample = "\n".join(f"    - ({t['file']} / {doc_sheet}) {t['rows'][0]}\n      …" for t in tables[:2])
    emit("text", sample + f"\n    (the second entry is {tables[1]['sheet'] if len(tables) > 1 else '…'}'s table)")
    note("the checker in steps 5 and 6 prints each attached table's label next to the warehouse its numbers belong "
         "to; the rows say SKU, Item, On hand, Reorder point — nothing names the warehouse")
    sited = index_tables(cli, world["sited"], "stock on hand by warehouse", echo=True)
    if sited:
        emit("text", f"\n  the same sheet with a Site column (sheet_name “{sited[0]['sheet']}”):\n"
             + "\n".join("    " + r for r in sited[0]["rows"][:3]))
    emit("index", "", tables=tables, sited=sited, sample=sample)
    return dict(tables=tables, sited=sited)


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
    res = router_call(cli, ["chat", q["say"], "--model", MODEL, "--max-tokens", "120", "--temperature", "0",
                            "--body", str(write_body().relative_to(HERE)), "--boundary", boundary], echo)
    if not res:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, from_sites=[])
    u = res.get("usage") or {}
    text = (res["choices"][0]["message"]["content"] or "").strip()
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return dict(q=q["id"], reply=text, prompt_tokens=u.get("prompt_tokens", 0), **grade(d, q, text))


LABEL = re.compile(r"\(([^()\n]*?\.xlsx) / ([^()\n]+)\)")


def check(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given, and read which sheets were
    attached. Its own call: what it is given can differ from what the answer call was given."""
    d = load_data()
    res = router_call(cli, ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
                            "--system", d["checker"], "--boundary", boundary], echo)
    if not res:
        return dict(readable=False, entries=[], declined=False, raw="")
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(text, u.get("input_tokens", 0))


ON_HAND = re.compile(r"SKU: ([A-Z]{2}-\d{4}), [^\n]*?On hand: (\d+)")


def whose_rows(d: dict, text: str) -> str | None:
    """The warehouse an attached table belongs to, read from its numbers (every count in the data is distinct)."""
    votes: dict[str, int] = {}
    for sku, n in ON_HAND.findall(text):
        for s in d["sites"]:
            if s["on_hand"].get(sku) == int(n):
                votes[s["site"]] = votes.get(s["site"], 0) + 1
    return max(votes, key=votes.get) if votes else None


def read_attached(text: str, tokens: int = 0) -> dict:
    """Each attached table the checker copied out: the sheet its label names, and the warehouse its numbers belong
    to. "NONE" is not proof that nothing was attached — the checker has written NONE and then answered from a
    table — so it is kept apart as "declined"."""
    d = load_data()
    parts = text.split("```")
    raw = text[:6000]
    if len(parts) < 3:
        return dict(readable=False, entries=[], declined=False, raw=raw, tokens=tokens)
    block = parts[1]
    if block.strip() == "NONE":
        return dict(readable=False, entries=[], declined=True, raw=raw, tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    entries = []
    for k, m in enumerate(marks):
        body = block[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(block)]
        entries.append(dict(label=m.group(2).strip(), rows=whose_rows(d, body)))
    return dict(readable=True, entries=entries, declined=False, raw=raw, tokens=tokens)


def entry_text(e: dict) -> str:
    if e["rows"] == e["label"]:
        return f"{e['rows']}'s rows as “{e['label']}”"
    return f"{e['rows'] or '?'}'s rows labelled “{e['label']}” ⚠"


def attached_state(checks: list[dict]) -> str:
    c = next((c for c in checks if c["readable"]), None)
    if c:
        return ("attached: " + ", ".join(entry_text(e) for e in c["entries"])) if c["entries"] else "no sheet attached"
    return "declined to copy (wrote NONE)" if any(c.get("declined") for c in checks) else "unreadable"


def ask_all(cli: CLI, world: dict, arm: str) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    rows = []
    for n, q in enumerate(d["questions"]):
        truth = site_of(d, q["site"])["on_hand"][q["sku"]]
        emit("text", f"\n  {q['id']}  {q['say']}   (count: {truth:,})")
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
        pick = next((a for a in answers if a["verdict"] in ("other", "mixed")), answers[0])
        whose = f"  ← {', '.join(pick['from_sites'])}'s count" if pick["from_sites"] else ""
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:130] + "”" + whose)
        row = dict(q=q["id"], arm=arm, site=q["site"], sku=q["sku"], answers=answers, checks=checks)
        rows.append(row)
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], site=q["site"], sku=q["sku"], truth=truth, attached=state,
             entries=next((c["entries"] for c in checks if c["readable"]), []),
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"], from_sites=a["from_sites"])
                      for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    ans = [a for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for a in ans) for k in ("right", "other", "mixed", "none")}
    out["answers"] = len(ans)
    chk = [c for r in rows for c in r["checks"]]
    ents = [e for c in chk if c["readable"] for e in c["entries"]]
    out["checks"] = dict(readable=sum(c["readable"] for c in chk), declined=sum(bool(c.get("declined")) for c in chk),
                         asked_attached=sum(any(e["rows"] == r["site"] for e in c["entries"])
                                            for r in rows for c in r["checks"] if c["readable"]),
                         entries=len(ents), mislabelled=sum(e["rows"] != e["label"] for e in ents))
    return out


def tally_line(t: dict) -> str:
    c = t["checks"]
    return (f"{t['right']}/{t['answers']} right · {t['other']} another warehouse's count"
            + (f" · {t['mixed']} gave both" if t["mixed"] else "") + f" · {t['none']} no figure  ·  "
            f"checker: {c['mislabelled']} of {c['entries']} attached tables under another warehouse's label, "
            f"asked warehouse's rows attached {c['asked_attached']}/{c['readable']}"
            + (f" ({c['declined']} declined to copy)" if c["declined"] else ""))


# --------------------------------------------------------------------------- step 7: where the wrong numbers came from

def provenance(rows: list[dict]) -> dict:
    """For every answer that gave another warehouse's count: whose count it was, and how the checker saw that
    warehouse's table attached for the same question (a separate call, so a hint, not proof): under the asked
    warehouse's label, under another label, or not at all."""
    d = load_data()
    lines, pairs = [], {}
    n = dict(wrong=0, under_asked=0, under_other=0, not_seen=0, unread=0)
    for r in rows:
        if r["arm"] != "tabs":
            continue
        q = next(x for x in d["questions"] if x["id"] == r["q"])
        c = next((c for c in r["checks"] if c["readable"]), None)
        for a in r["answers"]:
            if a["verdict"] not in ("other", "mixed"):
                continue
            n["wrong"] += 1
            src = a["from_sites"][0]
            for s in a["from_sites"]:
                pairs[(q["site"], s)] = pairs.get((q["site"], s), 0) + 1
            if c is None:
                n["unread"] += 1
                seen = "checker unreadable"
            else:
                hit = [e for e in c["entries"] if e["rows"] == src]
                if any(e["label"] == q["site"] for e in hit):
                    n["under_asked"] += 1
                    seen = f"{src}'s rows were attached labelled “{q['site']}”"
                elif hit:
                    n["under_other"] += 1
                    seen = f"{src}'s rows were attached labelled “{hit[0]['label']}”"
                else:
                    n["not_seen"] += 1
                    seen = f"checker did not see {src}'s rows"
            v = site_of(d, src)["on_hand"][q["sku"]]
            lines.append(f"    {q['id']} asked {q['site']:<8} {q['sku']}  answered {v:>5,} = {src + '’s count':<16} {seen}")
    if lines:
        emit("text", "\n".join(lines))
        emit("text", "\n    " + "   ".join(f"{a} → {b}: {k}" for (a, b), k in sorted(pairs.items())))
        note(f"{n['under_asked']} of {n['wrong']} wrong answers: the checker saw the other warehouse's rows under the "
             f"asked warehouse's own label; {n['under_other']} under a third label; {n['not_seen']} not seen; "
             f"{n['unread']} checker unreadable")
    else:
        note("no answer gave another warehouse's count this run")
    out = dict(n, pairs=[dict(asked=a, answered=b, n=k) for (a, b), k in sorted(pairs.items())])
    emit("provenance", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_numbers(d)
    banner(2, TOTAL, "Set up — a project for the count as kept, one with a Site column, a boundary each")
    world = setup(cli)

    banner(3, TOTAL, f"What the team keeps — the {d['week']} stock count, one sheet per warehouse")
    store(cli, world)

    banner(4, TOTAL, "The count in the index — one table per sheet")
    show_index(cli, world)

    rows: list[dict] = []
    tallies = {}
    for step, (arm, label) in zip((5, 6), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {label}")
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×; "
             f"a checker call ({CHECK_MODEL}) copies out what it was given")
        got = ask_all(cli, world, arm)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {SHORT[arm]}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(7, TOTAL, "Where the wrong numbers came from")
    note("each wrong count matched to the warehouse it belongs to — every count in the data is distinct")
    prov = provenance(rows) if rows else {}

    banner(8, TOTAL, "Scoreboard")
    for arm, label in ARMS:
        if arm in tallies:
            emit("text", f"  {SHORT[arm]:<12} {tally_line(tallies[arm])}")
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, provenance=prov, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and checker reading: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies, provenance=prov)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_sheet(cli: CLI, name: str) -> dict:
    """One warehouse's sheet as the index holds it, in the workbook as kept and with the Site column."""
    d = load_data()
    s = site_of(d, name)
    world = load_world(cli)
    q = f"{s['site']} stock on hand"
    tabs = [t for t in index_tables(cli, world["tabs"], q, echo=True) if t["sheet"] == s["site"]]
    sited = [t for t in index_tables(cli, world["sited"], q, echo=True) if t["sheet"] == s["site"]]
    emit("text", f"\n  {s['site']} — as kept: the table item with sheet_name “{s['site']}”, its rows as MemoryRouter attaches them")
    emit("text", "\n".join("    " + r for r in tabs[0]["rows"]) if tabs
         else "    (search did not return this sheet's table for this query)")
    emit("text", f"\n  {s['site']} — with the Site column:")
    emit("text", "\n".join("    " + r for r in sited[0]["rows"]) if sited
         else "    (search did not return this sheet's table for this query)")
    note("the label MemoryRouter puts in front of these rows is one sheet name for the whole file (step 5); "
         "only the second version still says which warehouse once the label is wrong")
    out = dict(site=s["site"], tabs=tabs[0]["rows"] if tabs else [], sited=sited[0]["rows"] if sited else [])
    emit("sheet", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = 0
    for name in ("tabs", "sited"):
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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "sheet", "cleanup"],
                    help="run = full demo (default); sheet <warehouse> = one warehouse's sheet in the index, as kept "
                         "and with the Site column; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for sheet: a warehouse (Leeds, Bristol, Glasgow, Cardiff)")
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
    if args.command == "sheet":
        if not args.args:
            die("usage: python3 demo.py sheet <warehouse>")
        banner(2, 2, f"The {args.args[0]} sheet in the index")
        show_sheet(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py sheet Bristol`; `python3 demo.py cleanup` removes the demo data.")


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

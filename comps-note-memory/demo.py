#!/usr/bin/env python3
"""comps-note-memory — the comps workbook the senior agent keeps, and the one the index reads.

Harrow & Vale Realty (a fictional brokerage) keeps its comparable sales in an Excel workbook that the senior agent,
Priya Nair, maintains, and puts it in shared memory so a newer agent's listing assistant (an ordinary model call
through MemoryRouter with `--boundary`) can answer "what's the lowest comp we can use for 12 Fenwick Street?".
Priya's judgement lives where Excel users put it: the reason a sale is not a usable comp is a cell note on its
price, a sale that fell through is a hidden row, and contracts that have not closed sit on a hidden sheet.

MemoryLake's index reads the workbook the other way round: not one word of the cell notes reaches the index, while
the hidden row and the hidden sheet are indexed like everything else — and MemoryRouter attaches the hidden sheet's
rows under the visible sheet's name. The assistant hands the agent the fire-damaged as-is sale as the lowest comp,
a contract price as the top sale, and the family sale as usable. Writing each note into an "Agent note" column
fixes most of it; the lowest-price question still comes back with the ruled-out price and a caveat. Leaving the
ruled-out rows out of the file is what holds.

The demo shows:
  · what the .xlsx holds, read from the file itself: the cell notes, the hidden row, the hidden sheet;
  · the two fixes, applied before upload: note_workbook() and usable_workbook();
  · the workbook in the index (`search --types document`) of all three projects;
  · what MemoryRouter hands the model for each question (a checker copies out what it was given);
  · eight questions an agent asks (six about ruled-out rows, two controls) through three boundaries — as saved,
    noted, usable — each asked several times.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (88 calls per run, roughly 50,000 tokens).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cnm"  # memorylake-usecases / comps-note-memory


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




MODEL = "gpt-4o-mini"        # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 3                     # answers per question per boundary
ARMS = ("saved", "noted", "usable")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "comps.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(d: dict, arm: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{d['file']}{d['arms'][arm]['suffix']}"


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def ppsf(c: dict) -> int:
    return int(c["price"] / c["sqft"] + 0.5)


# --------------------------------------------------------------------------- .xlsx: write, read, fix

FIXED = (2026, 10, 9, 9, 0, 0)  # zip entry timestamps, so the bytes are reproducible
X_NS = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml."
HIDDEN_SHEET = ' state="hidden"'
HIDDEN_ROW = ' hidden="1"'


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _unesc(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def col_name(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _cell(ref: str, v) -> str:
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_esc(str(v))}</t></is></c>'


def write_workbook(sheets: list[dict]) -> bytes:
    """A minimal .xlsx: each sheet {name, rows, hidden, hidden_rows, notes: [(ref, author, text)]}. Notes are
    legacy cell comments (comments part + the VML drawing Excel needs to show them)."""
    def part(z: zipfile.ZipFile, name: str, xml: str, decl: bool = True) -> None:
        z.writestr(zipfile.ZipInfo(name, FIXED),
                   ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' if decl else "") + xml,
                   compress_type=zipfile.ZIP_DEFLATED)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        noted = [i for i, s in enumerate(sheets, 1) if s.get("notes")]
        part(z, "[Content_Types].xml",
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>'
             f'<Override PartName="/xl/workbook.xml" ContentType="{CT}sheet.main+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{CT}worksheet+xml"/>'
                       for i in range(1, len(sheets) + 1))
             + "".join(f'<Override PartName="/xl/comments{i}.xml" ContentType="{CT}comments+xml"/>' for i in noted)
             + "</Types>")
        part(z, "_rels/.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="xl/workbook.xml"/></Relationships>')
        part(z, "xl/workbook.xml",
             f"<workbook {X_NS}><sheets>"
             + "".join(f'<sheet name="{_esc(s["name"])}" sheetId="{i}"{HIDDEN_SHEET if s.get("hidden") else ""} r:id="rId{i}"/>'
                       for i, s in enumerate(sheets, 1))
             + "</sheets></workbook>")
        part(z, "xl/_rels/workbook.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             + "".join(f'<Relationship Id="rId{i}" Type="{REL}worksheet" Target="worksheets/sheet{i}.xml"/>'
                       for i in range(1, len(sheets) + 1))
             + "</Relationships>")
        for i, s in enumerate(sheets, 1):
            hidden_rows = set(s.get("hidden_rows") or ())
            rows = "".join(f'<row r="{r}"{HIDDEN_ROW if r in hidden_rows else ""}>'
                           + "".join(_cell(f"{col_name(c)}{r}", v) for c, v in enumerate(row) if v not in (None, ""))
                           + "</row>" for r, row in enumerate(s["rows"], 1))
            legacy = '<legacyDrawing r:id="rId2"/>' if s.get("notes") else ""
            part(z, f"xl/worksheets/sheet{i}.xml", f"<worksheet {X_NS}><sheetData>{rows}</sheetData>{legacy}</worksheet>")
            if not s.get("notes"):
                continue
            part(z, f"xl/worksheets/_rels/sheet{i}.xml.rels",
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 f'<Relationship Id="rId1" Type="{REL}comments" Target="../comments{i}.xml"/>'
                 f'<Relationship Id="rId2" Type="{REL}vmlDrawing" Target="../drawings/vmlDrawing{i}.vml"/>'
                 "</Relationships>")
            authors = sorted({a for _, a, _ in s["notes"]})
            part(z, f"xl/comments{i}.xml",
                 '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><authors>'
                 + "".join(f"<author>{_esc(a)}</author>" for a in authors) + "</authors><commentList>"
                 + "".join(f'<comment ref="{ref}" authorId="{authors.index(a)}"><text><r><t xml:space="preserve">'
                           f'{_esc(a)}: {_esc(t)}</t></r></text></comment>' for ref, a, t in s["notes"])
                 + "</commentList></comments>")
            shapes = []
            for k, (ref, _, _) in enumerate(s["notes"]):
                c, r = col_index(ref), int(re.search(r"\d+", ref).group(0)) - 1
                shapes.append(
                    f'<v:shape id="_x0000_s{1025 + k}" type="#_x0000_t202" style="position:absolute;visibility:hidden" '
                    'fillcolor="#ffffe1" o:insetmode="auto"><v:fill color2="#ffffe1"/>'
                    '<v:shadow on="t" color="black" obscured="t"/><v:path o:connecttype="none"/>'
                    '<v:textbox style="mso-direction-alt:auto"><div style="text-align:left"></div></v:textbox>'
                    f'<x:ClientData ObjectType="Note"><x:MoveWithCells/><x:SizeWithCells/>'
                    f"<x:Anchor>{c + 1}, 15, {r}, 10, {c + 3}, 15, {r + 4}, 4</x:Anchor><x:AutoFill>False</x:AutoFill>"
                    f"<x:Row>{r}</x:Row><x:Column>{c}</x:Column></x:ClientData></v:shape>")
            part(z, f"xl/drawings/vmlDrawing{i}.vml",
                 '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
                 'xmlns:x="urn:schemas-microsoft-com:office:excel"><o:shapelayout v:ext="edit">'
                 '<o:idmap v:ext="edit" data="1"/></o:shapelayout><v:shapetype id="_x0000_t202" coordsize="21600,21600" '
                 'o:spt="202" path="m,l,21600r21600,l21600,xe"><v:stroke joinstyle="miter"/>'
                 '<v:path gradientshapeok="t" o:connecttype="rect"/></v:shapetype>' + "".join(shapes) + "</xml>",
                 decl=False)
    return buf.getvalue()


def saved_workbook(d: dict) -> bytes:
    """The workbook as the senior agent keeps it (data/make_workbooks.py writes it from comps.json)."""
    rows, hidden, notes = [d["columns"]], [], []
    for r, c in enumerate(d["comps"], 2):
        rows.append([c["address"], c["sold"], c["price"], c["sqft"], ppsf(c), c["beds"]])
        if c.get("hidden"):
            hidden.append(r)
        if c.get("note"):
            notes.append((f"C{r}", d["agent"], c["note"]))
    pending = [d["pending_columns"]] + [[p["address"], p["contract"], p["price"], p["sqft"], p["beds"]] for p in d["pending"]]
    return write_workbook([dict(name=d["sheet"], rows=rows, hidden_rows=hidden, notes=notes),
                           dict(name=d["pending_sheet"], rows=pending, hidden=True)])


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return _unesc(m.group(1)) if m else None


def _target(base: str, target: str) -> str:
    parts = base.split("/")[:-1]
    for p in target.split("/"):
        if p == "..":
            parts.pop()
        elif p and p != ".":
            parts.append(p)
    return "/".join(parts) if not target.startswith("/") else target.lstrip("/")


def _rels(z: zipfile.ZipFile, part: str) -> dict:
    """Relationship id → (type, target part) for one part."""
    head, tail = part.rsplit("/", 1)
    name = f"{head}/_rels/{tail}.rels"
    if name not in z.namelist():
        return {}
    xml = z.read(name).decode("utf-8")
    return {_attr(m, "Id"): (_attr(m, "Type") or "", _target(part, _attr(m, "Target") or ""))
            for m in re.findall(r"<Relationship\b[^>]*>", xml)}


def _num(s: str):
    f = float(s)
    return int(f) if f.is_integer() else f


def read_workbook(blob: bytes) -> dict:
    """What an .xlsx holds, read from the file itself (standard library): each sheet with its visibility, each row
    with its visibility and cell values, and each cell note (legacy comment) with its author and text. Works on
    files Excel saved (shared strings) as well as on this demo's own."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in re.findall(r"<si\b[^>]*>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S):
            shared.append(_unesc("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", si, re.S))))
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = _rels(z, "xl/workbook.xml")
    sheets, notes = [], []
    for tag in re.findall(r"<sheet\b[^>]*/?>", wb):
        name, state, rid = _attr(tag, "name"), _attr(tag, "state") or "visible", _attr(tag, "r:id")
        part = rels.get(rid, ("", ""))[1]
        xml = z.read(part).decode("utf-8")
        rows = []
        for rtag, body in re.findall(r"(<row\b[^>]*?)(?:/>|>(.*?)</row>)", xml, re.S):
            cells = {}
            for ctag, cbody in re.findall(r"(<c\b[^>]*?)(?:/>|>(.*?)</c>)", body or "", re.S):
                ref, t = _attr(ctag, "r"), _attr(ctag, "t")
                v = re.search(r"<v>(.*?)</v>", cbody or "", re.S)
                if t == "inlineStr":
                    val = _unesc("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", cbody or "", re.S)))
                elif t == "s" and v:
                    val = shared[int(v.group(1))]
                elif t in ("str", "e") and v:
                    val = _unesc(v.group(1))
                elif v:
                    val = _num(v.group(1))
                else:
                    continue
                cells[col_index(ref)] = val
            rows.append(dict(r=int(_attr(rtag, "r")), hidden=_attr(rtag, "hidden") in ("1", "true"), cells=cells))
        sheets.append(dict(name=name, hidden=state != "visible", rows=rows))
        for _, (typ, target) in _rels(z, part).items():
            if typ.endswith("/comments"):
                cx = z.read(target).decode("utf-8")
                authors = [_unesc(a) for a in re.findall(r"<author>(.*?)</author>", cx, re.S)]
                for ctag, cbody in re.findall(r"(<comment\b[^>]*>)(.*?)</comment>", cx, re.S):
                    a = int(_attr(ctag, "authorId") or 0)
                    notes.append(dict(sheet=name, ref=_attr(ctag, "ref"), author=authors[a] if a < len(authors) else "",
                                      text=" ".join(_unesc("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", cbody, re.S))).split())))
    return dict(sheets=sheets, notes=notes)


def grid(row: dict, width: int) -> list:
    return [row["cells"].get(c, "") for c in range(width)]


def _visible(wb: dict, keep_noted: bool) -> list[dict]:
    """The visible sheets and rows, each row with the text of every note on it (author prefix and all)."""
    out = []
    for s in wb["sheets"]:
        if s["hidden"] or not s["rows"]:
            continue
        width = max((max(r["cells"]) + 1 for r in s["rows"] if r["cells"]), default=0)
        on = {}
        for n in wb["notes"]:
            if n["sheet"] == s["name"]:
                on.setdefault(int(re.search(r"\d+", n["ref"]).group(0)), []).append(n["text"])
        head, body = s["rows"][0], [r for r in s["rows"][1:] if not r["hidden"]]
        out.append(dict(name=s["name"], width=width, head=grid(head, width),
                        rows=[(grid(r, width), " / ".join(on.get(r["r"], []))) for r in body
                              if keep_noted or not on.get(r["r"])]))
    return out


def note_workbook(blob: bytes, column: str) -> bytes:
    """Fix 1, before upload: write every cell note into a column on its row, and leave out the hidden rows and
    hidden sheets — the workbook as the agent sees it on screen, with the notes spelled out."""
    sheets = [dict(name=s["name"], rows=[s["head"] + [column]] + [g + [n] for g, n in s["rows"]])
              for s in _visible(read_workbook(blob), keep_noted=True)]
    return write_workbook(sheets)


def usable_workbook(blob: bytes) -> bytes:
    """Fix 2, before upload: only the rows the agent would use — no hidden rows or sheets, and no row that carries a
    cell note (on this team a note on a comp is the reason it is out; see the README to adapt the rule)."""
    sheets = [dict(name=s["name"], rows=[s["head"]] + [g for g, _ in s["rows"]])
              for s in _visible(read_workbook(blob), keep_noted=False)]
    return write_workbook(sheets)


def fixed_path(d: dict, arm: str) -> Path:
    return OUT / "fixed" / (d["file"] + d["arms"][arm]["suffix"])


def file_for(d: dict, arm: str) -> Path:
    return DATA / (d["file"] + ".xlsx") if arm == "saved" else fixed_path(d, arm)


def expected_rows(d: dict, arm: str) -> list[str]:
    """The data rows each copy must hold, as addresses (checked after each fix)."""
    if arm == "saved":
        return [c["address"] for c in d["comps"]] + [p["address"] for p in d["pending"]]
    return [c["address"] for c in d["comps"] if not c.get("hidden") and (arm == "noted" or not c.get("note"))]


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
NUM = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kKmM])?(?![\w-])")
CAVEAT = re.compile(r"not (?:be )?(?:used|usable|a (?:valid |usable |good )?comp|comparable|suitable|valid|arm)|exclud|"
                    r"should(?:n't| not)|do not use|don't use|non-arm|arm's[- ]length|family|fire|as[- ]is|lot\b|"
                    r"fell through|pending|under contract|contract|not (?:yet )?(?:closed|sold)|has(?:n't| not) (?:closed|sold)|"
                    r"ruled out|disregard|outlier", re.I)
NOT_SOLD = re.compile(r"not (?:yet )?(?:been )?(?:sold|closed)|has(?:n't| not) (?:yet )?(?:sold|closed)|under contract|"
                      r"pending|contract|no (?:recorded |closed )?sale|no record|not (?:been )?(?:in|listed|found|recorded|included)|"
                      r"(?:doesn't|does not) (?:appear|have)|no information|not available", re.I)
NO = re.compile(r"^\W*no\b|not (?:a )?(?:usable|suitable|valid|good|appropriate|recommended)|should(?:n't| not)|"
                r"cannot|can't|not be used|exclud|do not use|don't use", re.I)
YES = re.compile(r"^\W*yes\b", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def first_clause(a: str) -> str:
    return re.split(r"(?<=[A-Za-z0-9)])\.\s|;|\(|, (?:but|although|however|though|which|as|because)\b| but | - ", a)[0]


def numbers(s: str) -> set[int]:
    out = set()
    for m in NUM.finditer(s):
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        v *= 1000 if unit == "k" else 1_000_000 if unit == "m" else 1
        out.add(int(round(v)))
    return out


def grade(q: dict, reply: str, tokens: int, empty: int) -> dict:
    """empty = MemoryRouter attached nothing (same prompt tokens as the call without a boundary): not counted. On the
    answer line —
      value / address: right = the usable figure (row) · used = a ruled-out figure (row) given as the answer ·
        flagged = a ruled-out one given with a caveat (a note, "fell through", "under contract") · other = neither
      usable: right = no · used = yes · flagged = yes with a caveat · other = neither
      notsold: right = says it has not closed · used = the contract price given as the sale price · other = neither"""
    a = answer_line(reply)
    if tokens and empty and tokens <= empty:
        return dict(verdict="empty", clause=a)
    head, caveat = first_clause(a), bool(CAVEAT.search(a))
    if q["kind"] == "value":
        got, first = numbers(a), numbers(head)
        if first & set(q["bad"]):
            return dict(verdict="flagged" if caveat else "used", clause=a)
        if first & set(q["ok"]) or (got & set(q["ok"]) and not got & set(q["bad"])):
            return dict(verdict="right", clause=a)
        return dict(verdict="flagged" if got & set(q["bad"]) and caveat else "other", clause=a)
    if q["kind"] == "address":
        bad = any(norm(b) in norm(head) for b in q["bad"])
        if bad:
            return dict(verdict="flagged" if caveat else "used", clause=a)
        return dict(verdict="right" if any(norm(o) in norm(head) for o in q["ok"]) else "other", clause=a)
    if q["kind"] == "usable":
        if YES.search(a):
            return dict(verdict="flagged" if caveat else "used", clause=a)
        return dict(verdict="right" if NO.search(a) else "other", clause=a)
    # notsold
    if NOT_SOLD.search(a):
        return dict(verdict="right", clause=a)
    return dict(verdict="used" if numbers(a) & set(q["bad"]) else "other", clause=a)


MARK = {"right": "✓", "used": "✗", "flagged": "◐", "other": "·", "empty": "∅", None: "–"}
ORDER = ("used", "flagged", "other", "empty", "right", None)


def group_of(q: dict) -> str:
    return "control" if q.get("control") else "ruled-out"


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
        note("an earlier run left the comps behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for arm in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['company']} — Fenwick comps ({d['arms'][arm]['short']})",
                    "--custom-id", cid(arm), "--description", f"Comps workbook: {d['arms'][arm]['label']}. Demo data.",
                    scoped=True)
        b = cli.run("boundary", "create", "--name", f"Listing assistant: {d['arms'][arm]['short']}",
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


# --------------------------------------------------------------------------- step 3: the workbook

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


def import_arm(cli: CLI, world: dict, arm: str, item: str) -> None:
    project = world["project"][arm]
    try:
        cli.run("proj", "doc", "import", "--project", project, item, "--wait", scoped=True, echo=arm == ARMS[0])
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note(f"the import call for {arm} hit a network error; waiting for the document already submitted")
    wait_docs(cli, project, 1)


def workbook_view(d: dict) -> dict:
    """The as-saved workbook, read from the .xlsx itself: sheets, rows, what is hidden, and the cell notes."""
    wb = read_workbook((DATA / (d["file"] + ".xlsx")).read_bytes())
    comps = next(s for s in wb["sheets"] if s["name"] == d["sheet"])
    width = len(d["columns"])
    rows = []
    for r in comps["rows"][1:]:
        g = grid(r, width)
        n = [x for x in wb["notes"] if x["sheet"] == comps["name"] and int(re.search(r"\d+", x["ref"]).group(0)) == r["r"]]
        rows.append(dict(row=r["r"], cells=g, hidden=r["hidden"], note=n[0]["text"] if n else "", ref=n[0]["ref"] if n else ""))
    hidden_sheets = [dict(name=s["name"], head=grid(s["rows"][0], len(s["rows"][0]["cells"])),
                          rows=[grid(r, len(s["rows"][0]["cells"])) for r in s["rows"][1:]])
                     for s in wb["sheets"] if s["hidden"]]
    return dict(file=d["file"] + ".xlsx", sheet=comps["name"], columns=d["columns"], rows=rows,
                hidden_sheets=hidden_sheets, notes=wb["notes"], sheets=[(s["name"], s["hidden"]) for s in wb["sheets"]])


def show_workbook(d: dict, v: dict) -> None:
    out = [f"\n  {v['file']} — {d['agent']}'s comps workbook, saved {d['saved']}. Sheets: "
           + ", ".join(f"“{n}”" + (" (hidden)" if h else "") for n, h in v["sheets"]),
           f"    sheet “{v['sheet']}”  " + " · ".join(v["columns"])]
    for r in v["rows"]:
        c = r["cells"]
        flag = "  [hidden row]" if r["hidden"] else (f"  [note on {r['ref']}]" if r["note"] else "")
        out.append(f"    {r['row']:>2}  {c[0]:<20} {c[1]}  {c[2]:>8,}  {c[3]:>5,} sq ft{flag}")
    out.append("\n    cell notes (the red triangle in Excel; hover to read):")
    for n in v["notes"]:
        out.append(f"      {n['ref']}  “{n['text']}”")
    hid = next((c for c in d["comps"] if c.get("hidden")), None)
    if hid:
        out.append(f"    hidden row: {hid['address']} — {hid['why']}")
    for s in v["hidden_sheets"]:
        out.append(f"    hidden sheet “{s['name']}”: " + "; ".join(f"{r[0]} — {r[1]} at {r[2]:,}" for r in s["rows"]))
    emit("text", "\n".join(out))


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    src = DATA / (d["file"] + ".xlsx")
    if not src.exists():
        die(f"missing data/{src.name}; run `python3 data/make_workbooks.py`")
    v = workbook_view(d)
    want_notes = [c["address"] for c in d["comps"] if c.get("note")]
    if len(v["notes"]) != len(want_notes) or [r["cells"][0] for r in v["rows"] if r["hidden"]] != \
            [c["address"] for c in d["comps"] if c.get("hidden")] or len(v["hidden_sheets"]) != 1:
        die(f"data/{src.name} does not hold the notes / hidden parts comps.json lists; run `python3 data/make_workbooks.py`")
    show_workbook(d, v)
    blob = src.read_bytes()
    (OUT / "fixed").mkdir(parents=True, exist_ok=True)
    for arm, fix in (("noted", lambda b: note_workbook(b, d["note_column"])), ("usable", usable_workbook)):
        fixed = fix(blob)
        wb = read_workbook(fixed)
        got = [g[0] for s in wb["sheets"] for g in (grid(r, 1) for r in s["rows"][1:])]
        if wb["notes"] or any(s["hidden"] for s in wb["sheets"]) or any(r["hidden"] for s in wb["sheets"] for r in s["rows"]) \
                or got != expected_rows(d, arm):
            die(f"{arm}: the fixed workbook is not what comps.json says: {got}")
        fixed_path(d, arm).write_bytes(fixed)
    noted_n = len(expected_rows(d, "noted"))
    note(f"fix 1, note_workbook(): each of the {len(v['notes'])} cell notes written into an “{d['note_column']}” column; "
         f"the hidden row and the hidden sheet left out → {noted_n} rows")
    note(f"fix 2, usable_workbook(): only rows with no note, nothing hidden → {len(expected_rows(d, 'usable'))} rows. "
         f"Both read back and checked → {(OUT / 'fixed').relative_to(HERE)}/")
    t0 = time.time()
    items = {}
    for k, arm in enumerate(ARMS):
        items[arm] = cli.run("lib", "upload", os.path.relpath(file_for(d, arm)), "--name", lib_name(d, arm),
                             "--on-conflict", "overwrite", echo=k == 0)["item_id"]
    note("3 uploads: the workbook as saved, noted and usable; each project imports its own")
    with ThreadPoolExecutor(len(ARMS)) as pool:
        for fut in [pool.submit(import_arm, cli, world, arm, items[arm]) for arm in ARMS]:
            fut.result()
    note(f"all three indexed in {int(time.time() - t0)} s")
    emit("memory", "", workbook=v, arms={a: dict(label=d["arms"][a]["label"], short=d["arms"][a]["short"],
                                                 rows=expected_rows(d, a)) for a in ARMS},
         hidden=next((c for c in d["comps"] if c.get("hidden")), None))
    return v


# --------------------------------------------------------------------------- step 4: the workbook in the index

NOTE_WORDS = re.compile(r"arm's|family|fire|as-is|buildable|exclude|do not use|not comparable|fell through|"
                        + re.escape("Priya"), re.I)


def index_tables(cli: CLI, project: str, query: str, echo: bool = False) -> list[dict]:
    """Every table item `search --types document` returns for the project: its sheet name and its rows."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "6",
                  scoped=True, echo=echo) or {}
    out, seen = [], set()
    for doc in res.get("documents") or []:
        for it in doc.get("items") or []:
            text = "\n".join((ch.get("text") or "") for ch in (it.get("highlight") or {}).get("chunks") or []).strip()
            key = (it.get("sheet_name"), text)
            if text and key not in seen:
                seen.add(key)
                out.append(dict(file=doc.get("document_name") or "", type=it.get("type"), sheet=it.get("sheet_name"),
                                doc_sheet=doc.get("sheet_name"), rows=text.splitlines(),
                                summary=(doc.get("document_summary") or "").strip()))
    return out


def arm_in_index(cli: CLI, world: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    name = lib_name(d, arm)
    tables = [t for t in index_tables(cli, world["project"][arm], "comparable sales sold price square feet", echo)
              if t["file"] == name]
    rows = [r for t in tables for r in t["rows"]]
    blob = "\n".join(rows)
    hid = [c["address"] for c in d["comps"] if c.get("hidden")]
    return dict(arm=arm, name=name, found=bool(tables), tables=tables, rows=len(rows),
                note_words=len(NOTE_WORDS.findall(blob)),
                hidden_row=[a for a in hid if a in blob],
                pending=[p["address"] for p in d["pending"] if p["address"] in blob],
                noted=[c["address"] for c in d["comps"] if c.get("note") and c["address"] in blob],
                summary=tables[0]["summary"] if tables else "")


def show_index(cli: CLI, world: dict) -> dict:
    d = load_data()
    for attempt in range(6):
        with ThreadPoolExecutor(3) as pool:
            views = list(pool.map(lambda a: arm_in_index(cli, world, a, a == ARMS[0] and attempt == 0), ARMS))
        if all(v["found"] for v in views):
            break
        time.sleep(10)
    if not any(v["found"] for v in views):
        die("search did not return the workbooks yet; run again in a minute")
    by = {v["arm"]: v for v in views}
    s = by["saved"]
    out = [f"\n  as saved — {s['name'][len(PREFIX) + 1:]}"]
    for t in s["tables"]:
        out.append(f"    table item · sheet_name “{t['sheet']}” · {len(t['rows'])} row(s)")
        for r in t["rows"]:
            hit = next((a for a in s["hidden_row"] if a in r), None) or next((a for a in s["pending"] if a in r), None)
            tag = "   ← hidden row" if hit in s["hidden_row"] else "   ← hidden sheet" if hit else ""
            out.append(f"      {r[:118]}{tag}")
    out.append(f"    words from the {len(load_data()['comps']) and sum(1 for c in d['comps'] if c.get('note'))} cell notes "
               f"anywhere in the index text: {s['note_words']}")
    if s["summary"]:
        out.append("    the index's own summary: “" + " ".join(s["summary"].split())[:300] + "”")
    emit("text", "\n".join(out))
    for arm in ("noted", "usable"):
        v = by[arm]
        sample = next((r for t in v["tables"] for r in t["rows"] if d["note_column"] in r and "Quarry" in r), "")
        emit("text", f"\n  {d['arms'][arm]['short']:<8} {v['rows']} row(s) · hidden row {len(v['hidden_row'])} · "
                     f"hidden sheet {len(v['pending'])} · noted rows {len(v['noted'])}"
             + (f"\n           e.g. “{sample[:150]}”" if sample else ""))
    note("the index reads the cells and nothing on them: no note reaches it, while the hidden row and the hidden "
         "sheet are indexed like the rest. MemoryRouter attaches this text")
    emit("index", "", arms=views)
    return dict(arms=views)


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


def chat(cli: CLI, q: dict, boundary: str | None, echo: bool = False) -> tuple[str, int] | None:
    d = load_data()
    args = ["chat", q["say"], "--model", MODEL, "--max-tokens", "120", "--temperature", "0", "--system", d["system"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI) -> dict:
    """Each question once without a boundary: its prompt tokens are the system prompt and the question alone. A
    boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda kq: chat(cli, kq[1], None, kq[0] == 0), enumerate(d["questions"])))
    return {q["id"]: (g[1] if g else 0) for q, g in zip(d["questions"], got)}


def answer(cli: CLI, q: dict, boundary: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, q, boundary, echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, clause="")
    text, tokens = got
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(q, text, tokens, empty))


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- step 5: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.xlsx)(?: / ([^()\n]+))?\)")


def read_attached(d: dict, text: str, tokens: int) -> dict:
    """What the checker copied out: each attached entry's label, and the rows in it that the agent ruled out. NONE
    with only the checker's own prompt is "nothing attached"; NONE otherwise is "declined"."""
    parts = text.split("```")
    if len(parts) < 3:
        return dict(readable=False, declined=False, entries=[], raw=text[:4000], tokens=tokens)
    block = parts[1]
    if block.strip() == "NONE":
        return dict(readable=False, declined=True, entries=[], raw=text[:4000], tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    # The checker sometimes copies an entry without its "(file / sheet)" label: keep it, marked as such.
    spans = [(m.group(1)[len(PREFIX) + 1:], (m.group(2) or "").strip(), m.end(),
              marks[k + 1].start() if k + 1 < len(marks) else len(block)) for k, m in enumerate(marks)]
    if not spans and "Address:" in block:
        spans = [(None, "", 0, len(block))]
    entries = []
    for file, label, start, end in spans:
        body = block[start:end]
        lines = [l_ for l_ in body.splitlines() if "Address:" in l_]
        entries.append(dict(
            file=file, label=label, rows=len(lines),
            hidden_row=[c["address"] for c in d["comps"] if c.get("hidden") and c["address"] in body],
            pending=[p["address"] for p in d["pending"] if p["address"] in body],
            noted=[c["address"] for c in d["comps"] if c.get("note") and c["address"] in body],
            note_words=len(NOTE_WORDS.findall(body))))
    return dict(readable=True, declined=False, entries=entries, raw=text[:4000], tokens=tokens)


def check(cli: CLI, q: dict, boundary: str, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer calls were given."""
    d = load_data()
    res = router_call(cli, ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
                            "--system", d["checker"], "--boundary", boundary], echo)
    if not res:
        return dict(readable=False, declined=False, entries=[], raw="", tokens=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(d, text, u.get("input_tokens", 0))


def entry_line(e: dict) -> str:
    bits = [f"{e['rows']} row(s)"]
    if e["noted"]:
        bits.append(f"{len(e['noted'])} with a note in Excel")
    if e["hidden_row"]:
        bits.append("the hidden row (" + ", ".join(e["hidden_row"]) + ")")
    if e["pending"]:
        bits.append("hidden-sheet contracts (" + ", ".join(e["pending"]) + ")")
    bits.append(f"note words {e['note_words']}")
    if not e["file"]:
        return "(copied without its file label) " + " · ".join(bits)
    return f"({e['file']}" + (f" / {e['label']}" if e["label"] else "") + ") " + " · ".join(bits)


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = [q for q in d["questions"] if not q.get("control")]
    note(f"for each question about a ruled-out row, {CHECK_MODEL} is asked through the as-saved boundary to copy out "
         "everything above the question (its own call: the attachment can differ from the answer calls')")
    first = check(cli, qs[0], world["boundary"]["saved"], echo=True)
    with ThreadPoolExecutor(6) as pool:
        rest = list(pool.map(lambda q: check(cli, q, world["boundary"]["saved"]), qs[1:]))
    out = []
    for q, c in zip(qs, [first] + rest):
        if c["readable"]:
            body = "\n".join("      " + entry_line(e) for e in c["entries"]) or "      (no workbook entry)"
        else:
            body = "      declined to copy (wrote NONE)" if c["declined"] else "      unreadable"
        emit("text", f"\n  {q['id']}  {q['say']}\n{body}")
        out.append(dict(q=q["id"], **{k: c[k] for k in ("readable", "declined", "entries", "tokens")}))
    entries = [e for c in out for e in c["entries"]]
    pend = [e for e in entries if e["pending"]]
    labels = sorted({e["label"] for e in pend if e["label"]})
    note(f"{sum(c['readable'] for c in out)}/{len(out)} copies readable; words from the cell notes in them: "
         f"{sum(e['note_words'] for e in entries)}; the hidden row in {sum(bool(e['hidden_row']) for e in entries)} "
         f"entries; hidden-sheet contracts in {len(pend)}"
         + (f", labelled " + ", ".join(f"“{x}”" for x in labels) if labels else ""))
    emit("attached", "", checks=out, pending_labels=labels)
    return out


# --------------------------------------------------------------------------- steps 6–8: ask

def ask_all(cli: CLI, world: dict, arm: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][arm]
    qs = d["questions"]
    first = answer(cli, qs[0], boundary, base.get(qs[0]["id"], 0), echo=True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(answer, cli, q, boundary, base.get(q["id"], 0))
                for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        emit("text", f"\n  {q['id']}  {q['say']}\n      truth: {q['truth']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        emit("text", f"      answers {' '.join(MARK[a['verdict']] for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], group=group_of(q), arm=arm, answers=answers))
        emit("asked", "", q=q["id"], group=group_of(q), arm=arm, say=q["say"], truth=q["truth"],
             answers=[dict(verdict=a["verdict"], reply=a["reply"], tokens=a["prompt_tokens"]) for a in answers])
    return rows


GROUPS = ("ruled-out", "control")
VERDICTS = ("right", "used", "flagged", "other", "empty")


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
    return out


def tally_line(t: dict) -> str:
    y, c = t["ruled-out"], t["control"]
    graded = lambda g: g["answers"] - g["empty"]
    return (f"ruled-out rows: {y['right']}/{graded(y)} right · {y['used']} used as a comp · {y['flagged']} given "
            f"with a caveat" + (f" · {y['other']} other" if y["other"] else "") + (f" · {y['empty']} ∅" if y["empty"] else "")
            + f"  ·  controls: {c['right']}/{graded(c)}" + (f" · ∅ {c['empty']}" if c["empty"] else ""))


def instead(rows: list[dict]) -> list[str]:
    """What the assistant handed the agent instead of the right answer: one line per distinct reply, worst first.
    ∅ replies (nothing attached: the model on its own) come last — they are not graded, but they are what a user
    would read."""
    order = ["used", "flagged", "other", "empty"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text in seen:
                continue
            seen.add(text)
            found.append((order.index(a["verdict"]), f"    {MARK[a['verdict']]} {r['q']} [{r['arm']}] “{text[:150]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 9
LOCK = threading.Lock()


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for each copy of the comps workbook, a boundary over each")
    world = setup(cli)

    banner(3, TOTAL, "The comps workbook — what the senior agent sees in Excel")
    store(cli, world)

    banner(4, TOTAL, "The workbook in the index — what is left of the notes, what of the hidden parts?")
    index = show_index(cli, world)

    banner(5, TOTAL, "What MemoryRouter hands the model — as saved")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies = {}
    base: dict = {}
    for step, arm in zip((6, 7, 8), ARMS):
        banner(step, TOTAL, f"Ask through MemoryRouter — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        if not base:
            note("first, each question once without a boundary: those prompt tokens are the system prompt and the "
                 "question alone, so a boundary call with the same count had nothing attached (∅)")
            base = baselines(cli)
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ✗ a ruled-out "
             "row used as a comp · ◐ given with a caveat · · other · ∅ nothing attached")
        got = ask_all(cli, world, arm, base)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(9, TOTAL, "Scoreboard")
    by = {v["arm"]: v for v in index["arms"]}
    for arm in ARMS:
        v = by[arm]
        text = (f"  {d['arms'][arm]['short']:<9} in the index: {v['rows']} rows · note words {v['note_words']} · "
                f"hidden row {len(v['hidden_row'])} · hidden-sheet contracts {len(v['pending'])}")
        if arm in tallies:
            text += f"\n  {'':<9} {tally_line(tallies[arm])}"
        emit("text", text)
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant handed the agent instead (✗ used as a comp · ◐ with a caveat · · other · "
                     "∅ nothing attached, the model on its own):\n"
             + "\n".join(said[:12]) + (f"\n    … {len(said) - 12} more in out/report.json" if len(said) > 12 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, index=index["arms"], attached=attached, baselines=base, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         index=index["arms"], instead=said[:12])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: what the .xlsx holds, and what each fix writes instead."""
    d = load_data()
    v = workbook_view(d)
    show_workbook(d, v)
    blob = (DATA / (d["file"] + ".xlsx")).read_bytes()
    fixed = {}
    for arm, fix in (("noted", lambda b: note_workbook(b, d["note_column"])), ("usable", usable_workbook)):
        wb = read_workbook(fix(blob))
        s = wb["sheets"][0]
        width = len(s["rows"][0]["cells"])
        rows = [grid(r, width) for r in s["rows"]]
        fixed[arm] = rows
        emit("text", f"\n  {d['arms'][arm]['short']} — sheets: " + ", ".join(f"“{x['name']}”" for x in wb["sheets"])
             + f", {len(rows) - 1} rows")
        for r in rows[1:]:
            extra = f"  {d['note_column']}: “{r[-1]}”" if arm == "noted" and r[-1] else ""
            emit("text", f"    {r[0]:<20} {r[1]}  {r[2]:>8,}{extra}")
    emit("workbook", "", workbook=v, fixed=fixed)
    return dict(workbook=v, fixed=fixed)


def show_comp(cli: CLI, address: str) -> dict:
    """One address: what the workbook holds for it, and its row in the index text of all three projects."""
    d = load_data()
    world = load_world(cli)
    want = norm(address)
    every = [c["address"] for c in d["comps"]] + [p["address"] for p in d["pending"]]
    hit = next((a for a in every if want in norm(a)), None)
    if not hit:
        die(f"no address matching {address!r}; one of: {', '.join(every)}")
    c = next((c for c in d["comps"] if c["address"] == hit), None)
    p = next((p for p in d["pending"] if p["address"] == hit), None)
    if c:
        how = ("hidden row — " + c["why"]) if c.get("hidden") else (f"cell note: “{c['note']}”" if c.get("note") else "no note, not hidden")
        emit("text", f"\n  {hit} — in the workbook: sold {c['sold']} for {c['price']:,}; {how}")
    else:
        emit("text", f"\n  {hit} — in the workbook: hidden sheet “{d['pending_sheet']}”, under contract {p['contract']} at {p['price']:,}")
    views = []
    for k, arm in enumerate(ARMS):
        tables = [t for t in index_tables(cli, world["project"][arm], f"{hit} sale price", echo=k == 0)
                  if t["file"] == lib_name(d, arm)]
        line = next(((t["sheet"], r) for t in tables for r in t["rows"] if hit in r), None)
        emit("text", f"  {d['arms'][arm]['short']:<9} " + (f"sheet_name “{line[0]}”: “{line[1][:140]}”" if line else "not in the index text"))
        views.append(dict(arm=arm, sheet=line[0] if line else None, row=line[1] if line else None))
    out = dict(address=hit, comp=c, pending=p, views=views)
    emit("lookup", "", **out)
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
    shutil.rmtree(OUT / "fixed", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "comp", "workbook", "cleanup"],
                    help="run = full demo (default); comp <address> = one address in the workbook and in the index of "
                         "all three projects; workbook = what the .xlsx holds and what the two fixes write (offline); "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for comp: an address, e.g. \"Mill Pond\" or \"16 Fenwick\"")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "workbook":
        banner(1, 1, "Workbook — what the .xlsx holds, and what the two fixes write (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "comp":
        if not args.args:
            die("usage: python3 demo.py comp <address>")
        banner(2, 2, f"Comp “{' '.join(args.args)}” — the workbook, and its row in the three projects' index")
        show_comp(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py comp \"Mill Pond\"`; `python3 demo.py cleanup` removes the demo data.")


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

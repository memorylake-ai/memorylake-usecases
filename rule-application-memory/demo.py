#!/usr/bin/env python3
"""rule-application-memory — the right protocol page is in front of the model; applying it is where it goes wrong.

Corvane Therapeutics (a fictional drug developer) keeps the protocol of its Phase II trial CVT-118-201 in shared
memory: six short pages — dosing, prohibited medicines, liver safety, eligibility, visit schedule, SAE reporting. A
trial assistant (an ordinary model call through MemoryRouter) answers the study team's questions from them.

Ask it for a rule ("What is the maximum dose per infusion?") and it is right. Ask it to apply that rule to a
participant ("1042 weighed 93 kg this morning — what dose today?") and it answers "180 mg" in the same confident
one-liner, with the page that says "never more than 160 mg" right there in its prompt. Retrieval is not the problem:
the demo takes it out of the question by pasting the page the index found into the prompt itself.

The demo asks fifteen questions — six about a rule, seven that apply one to a participant, two the protocol does not
cover — three ways:
  · the "Do not guess" prompt, MemoryRouter attaching what it finds (`router chat --boundary`);
  · the same prompt, with the right page pasted in by the app (`search --types document --top-k 1`);
  · the same page, and the prompt asks for the rule word for word, then one step per line, then the answer. The app
    checks that the quoted rule is really on the page; a step that goes wrong is there for a person to see.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 150 calls per run, roughly 35,000 tokens).
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

PREFIX = "mlu-ram"  # memorylake-usecases / rule-application-memory


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
        # A multi-line argument (a system prompt with a protocol page) is shown as its first line; it is printed in full
        # once, above the call.
        shown = [a.split("\n")[0] + f" […{a.count(chr(10))} more lines]" if "\n" in a else a for a in shown]
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
                parsed = self._parse(out)
                # A reply that should be JSON and isn't (seen once on `lib upload`, exit 0): retry what is safe to retry.
                if parsed is not None or not out.strip() or not retryable or attempt >= 4 or args[0] == "router":
                    break
                attempt += 1
                note(f"unexpected reply ({' '.join(out.split())[:80]}); retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        parsed = self._parse(out)
        return parsed if parsed is not None else (out if out.strip() else None)

    @staticmethod
    def _parse(out: str):
        """The JSON reply, also when something was printed before it; None when there is no JSON."""
        for start in (0, out.find("{"), out.find("[")):
            if start < 0:
                continue
            try:
                return json.loads(out[start:])
            except json.JSONDecodeError:
                continue
        return None

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
RUNS = 3                     # answers per question per arm
PROJECTS = ("protocol",)                   # the MemoryLake project the demo creates
ARMS = ("boundary", "page", "work")        # the three ways of asking
MAX_TOKENS = {"boundary": "150", "page": "150", "work": "500"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "trial.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(key: str) -> str:
    """The Library file name: prefixed, so cleanup can find it."""
    return f"{PREFIX}-{key}.md"


def page_path(key: str) -> Path:
    return DATA / "protocol" / f"{key}.md"


def page_key(name: str) -> str | None:
    """`mlu-ram-dosing.md` → `dosing`."""
    name = name or ""
    if name.startswith(PREFIX + "-") and name.endswith(".md"):
        return name[len(PREFIX) + 1:-3]
    return None


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-").replace("“", '"')
                    .replace("”", '"').lower().split())


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\W*answer\W*:\s*(.+)$", re.I | re.M)
# The reply says the protocol does not have it.
NO_RECORD = re.compile(r"no record|no (?:information|protocol|data|details|entry|mention|rule)\b|"
                       r"not (?:been )?(?:available|found|mentioned|recorded|included|specified|provided|documented|"
                       r"listed|given|stated|covered)|"
                       r"(?:don't|do not|doesn't|does not) (?:have|appear|contain|include|mention|specify|cover)|"
                       r"there (?:is|are|was) no\b|unable to|cannot (?:find|provide|determine|confirm)|"
                       r"can't (?:find|provide)", re.I)
QUOTE = re.compile(r"[\"“]([^\"“”]{20,})[\"”]")


def answer_line(reply: str) -> str:
    """The last 'Answer:' line (the working comes first in the third setup); the first line if there is none."""
    found = ANSWER.findall(reply)
    line = found[-1] if found else (reply.strip().split("\n")[0] if reply.strip() else "")
    return " ".join(line.replace("**", "").split())


def grade(q: dict, reply: str) -> dict:
    """right = the protocol's answer (or “No record” where the protocol is silent) · wrong = another answer ·
    missed = “No record” although a page answers it · invented = an answer where the protocol is silent."""
    line = answer_line(reply)
    low = norm(line)
    said_none = bool(NO_RECORD.search(line))
    if q["kind"] == "none":
        v = "right" if said_none else "invented"
    elif said_none:
        v = "missed"
    elif any(re.search(p, low) for p in q["accept"]) and not any(re.search(p, low) for p in q["reject"]):
        v = "right"
    else:
        v = "wrong"
    return dict(clause=line, verdict=v, said_none=said_none)


def quote_check(reply: str, page: str) -> str:
    """Is the rule the reply quotes really on the page? `verbatim` · `not on the page` · `none` (no quote)."""
    m = QUOTE.search(reply)
    if not m:
        return "none"
    # A quote shortened with "..." counts when every piece of it is on the page.
    pieces = [x.strip(" .") for x in re.split(r"\.\.\.|…", norm(m.group(1))) if len(x.strip(" .")) >= 8]
    return "verbatim" if pieces and all(x in norm(page) for x in pieces) else "not on the page"


def steps_of(reply: str) -> list[str]:
    """The working: every line between the quote and the answer."""
    out = []
    for ln in reply.splitlines():
        s = " ".join(ln.split())
        if not s or ANSWER.match(ln) or QUOTE.fullmatch(s.strip()) or s.startswith(('"', "“")):
            continue
        out.append(s)
    return out


MARK = {"right": "✓", "wrong": "✗", "missed": "–", "invented": "✎", None: "·"}
ORDER = ("wrong", "invented", "missed", "right", None)
VERDICTS = ("right", "wrong", "missed", "invented")
KINDS = ("rule", "applied", "none")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "")


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
    if demo_boundaries(cli) or any(find_project(cli, p) for p in PROJECTS) or demo_files(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    p = cli.run("proj", "create", "--name", f"{d['study']} protocol", "--custom-id", cid("protocol"),
                "--description", f"{d['company']} trial protocol pages. Demo data.", scoped=True)
    world["project"]["protocol"] = p["id"]
    b = cli.run("boundary", "create", "--name", "Trial assistant: protocol", "--custom-id", cid("protocol"),
                "--projects", world["project"]["protocol"], scoped=True)
    world["boundary"]["protocol"] = b["id"]
    note("one project for the six protocol pages (documents) and one boundary over it, for "
         "`router chat … --boundary` in step 5")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    p = find_project(cli, "protocol")
    if not p or cid("protocol") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project={"protocol": p["id"]}, boundary={"protocol": bs[cid("protocol")]})


# --------------------------------------------------------------------------- step 3: the protocol

def project_docs(cli: CLI, project: str) -> list[dict]:
    page_ = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True, echo=False) or {}
    return page_.get("items") or []


def wait_docs(cli: CLI, project: str, names: set[str]) -> list[dict]:
    for attempt in range(72):
        docs = project_docs(cli, project)
        bad = [x for x in docs if x.get("status") == "error" and x.get("name") in names]
        if bad:
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        if names <= {x.get("name") for x in docs if x.get("status") == "okay"}:
            return docs
        time.sleep(5)
    die("the documents are still processing after 6 minutes; run again later")


def upload(cli: CLI, key: str, echo: bool = False) -> str:
    return cli.run("lib", "upload", os.path.relpath(page_path(key)), "--name", lib_name(key),
                   "--on-conflict", "overwrite", echo=echo)["item_id"]


def rule_of(text: str) -> str:
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#") and not ln.startswith("Owner:")]
    return body[0] if body else ""


def pages_view(d: dict) -> list[dict]:
    return [dict(key=p["key"], title=p["title"], file=f"{p['key']}.md",
                 first=rule_of(page_path(p["key"]).read_text(encoding="utf-8"))) for p in d["pages"]]


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for p in d["pages"]:
        text = page_path(p["key"]).read_text(encoding="utf-8")
        if p["title"] not in text or any(ord(ch) > 127 for ch in text):
            die(f"data/protocol/{p['key']}.md does not match trial.json (or is not plain ASCII)")
    view = pages_view(d)
    out = [f"\n  {d['company']} — protocol {d['study']}, {len(view)} pages:"]
    for v in view:
        out.append(f"    {v['file']:<16} {v['title']}")
        out.append(f"        {v['first'][:104]}" + ("…" if len(v["first"]) > 104 else ""))
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {p["key"]: upload(cli, p["key"], echo=k == 0) for k, p in enumerate(d["pages"])}
    note(f"{len(items)} pages uploaded to the Library; importing them into the protocol project")
    project = world["project"]["protocol"]
    try:
        cli.run("proj", "doc", "import", "--project", project, *items.values(), "--wait", scoped=True)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, {lib_name(p["key"]) for p in d["pages"]})
    note(f"indexed in {int(time.time() - t0)} s")
    emit("memory", "", pages=view, items=len(items))
    return dict(items=items)


# --------------------------------------------------------------------------- step 4: can the index find the page?

def search_page(cli: CLI, project: str, query: str, echo: bool = False) -> dict:
    """The top document `search --types document --top-k 1` returns for the question: its file and page text."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "1",
                  scoped=True, echo=echo) or {}
    docs = res.get("documents") or []
    if not docs:
        return dict(key=None, file=None, text="")
    doc = docs[0]
    text = "\n".join(c.get("text") or "" for it in doc.get("items") or []
                     for c in (it.get("highlight") or {}).get("chunks") or [])
    key = page_key(doc.get("document_name") or "")
    return dict(key=key, file=f"{key}.md" if key else doc.get("document_name"), text=text.strip())


def find_all(cli: CLI, world: dict) -> dict:
    d = load_data()
    rows, hits = [], {}
    for k, q in enumerate(d["questions"]):
        hit = search_page(cli, world["project"]["protocol"], q["say"], echo=k == 0)
        hits[q["id"]] = hit
        if q["page"]:
            tag = "← the page with the rule" if hit["key"] == q["page"] else f"← not it (the rule is in {q['page']}.md)"
        else:
            tag = "← closest page; the protocol does not cover this"
        emit("text", f"  {q['id']:<3} {q['say'][:70]:<70}  top 1: {hit['file'] or 'nothing':<15} {tag}")
        rows.append(dict(q=q["id"], file=hit["file"], key=hit["key"], right=hit["key"] == q["page"]))
    n = sum(r["right"] for r, q in zip(rows, d["questions"]) if q["page"])
    m = sum(1 for q in d["questions"] if q["page"])
    note(f"search finds the page with the rule for {n} of the {m} questions the protocol covers. The app keeps each "
         "question's top page: steps 6 and 7 paste it into the prompt")
    emit("found", "", rows=rows, right=n, of=m)
    return dict(rows=rows, right=n, of=m, hits=hits)


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


def chat(cli: CLI, say: str, system: str, boundary: str | None = None, max_tokens: str = "150",
         echo: bool = False) -> tuple[str, int] | None:
    args = ["chat", say, "--model", MODEL, "--max-tokens", max_tokens, "--temperature", "0", "--system", system]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI) -> dict:
    """Each question once without a boundary, with the same system prompt: those prompt tokens are the system prompt
    and the question alone. A boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    qs = d["questions"]
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda nq: chat(cli, nq[1]["say"], d["system"]["answer"], echo=nq[0] == 0), enumerate(qs)))
    return {q["id"]: (g[1] if g else 0) for q, g in zip(qs, got)}


def with_page(system: str, header: str, hit: dict) -> str:
    return f"{system}\n\n{header}\n{hit['text']}" if hit.get("text") else system


def ask(cli: CLI, world: dict, arm: str, q: dict, hit: dict, empty: int = 0, echo: bool = False) -> dict:
    """One answer. boundary: MemoryRouter attaches · page: the app pastes the search page · work: the same page, and the
    prompt asks for the rule word for word, then the steps."""
    d = load_data()
    if arm == "boundary":
        got = chat(cli, q["say"], d["system"]["answer"], world["boundary"]["protocol"], MAX_TOKENS[arm], echo)
    else:
        system = with_page(d["system"]["answer" if arm == "page" else "work"], d["page_header"], hit)
        got = chat(cli, q["say"], system, None, MAX_TOKENS[arm], echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, empty=False, clause="", said_none=False)
    text, tokens = got
    out = dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(q, text))
    out["empty"] = bool(arm == "boundary" and tokens and empty and tokens <= empty)
    out["attached"] = max(tokens - empty, 0) if arm == "boundary" else 0
    if arm == "work":
        out["quote"] = quote_check(text, hit.get("text") or "")
        out["steps"] = steps_of(text)
    return out


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- steps 5–7: ask

def ask_all(cli: CLI, world: dict, arm: str, hits: dict, base: dict) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    one = lambda q, echo=False: ask(cli, world, arm, q, hits.get(q["id"]) or {}, base.get(q["id"], 0), echo)
    first = one(qs[0], True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(one, q) for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        head = f"\n  {q['id']}  {q['say']}\n      protocol: {q['fact']}"
        line = f"      answers {' '.join(shown(a) for a in answers)}   e.g. “{pick['clause'][:150]}”"
        if arm == "work" and pick["verdict"] != "right" and pick.get("steps"):
            line += "\n" + "\n".join(f"        │ {s[:150]}" for s in pick["steps"][:5])
        if arm == "work" and q["page"]:
            quotes = [a.get("quote") for a in answers]
            line += f"\n      quoted rule: {sum(x == 'verbatim' for x in quotes)}/{len(quotes)} word for word on {hits[q['id']]['file']}"
        emit("text", head + "\n" + line)
        rows.append(dict(q=q["id"], qkind=q["kind"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qkind=q["kind"], page=q["page"], fact=q["fact"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], clause=a["clause"],
                           tokens=a["prompt_tokens"], quote=a.get("quote"), steps=a.get("steps")) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    """Per question kind: how many answers of each verdict."""
    out: dict = {}
    for kind in KINDS:
        ans = [a for r in rows if r["qkind"] == kind for a in r["answers"] if a["verdict"]]
        t = {v: sum(a["verdict"] == v for a in ans) for v in VERDICTS}
        t["answers"] = len(ans)
        t["empty"] = sum(bool(a.get("empty")) for a in ans)
        t["quoted"] = sum(a.get("quote") == "verbatim" for a in ans)
        out[kind] = t
    return out


def tally_line(t: dict, kind: str) -> str:
    u = t[kind]
    if not u["answers"]:
        return "—"
    s = f"{u['right']}/{u['answers']} ✓"
    for v, m in (("wrong", "✗"), ("missed", "–"), ("invented", "✎")):
        if u[v]:
            s += f" · {u[v]} {m}"
    return s


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the study team instead of the protocol's answer: one line per distinct reply."""
    order = ["wrong", "invented", "missed"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = a["clause"]
            if (r["arm"], r["q"], text[:150]) in seen:
                continue
            seen.add((r["arm"], r["q"], text[:150]))
            found.append((ARMS.index(r["arm"]), order.index(a["verdict"]),
                          f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:140]}”"))
    return [line_ for *_, line_ in sorted(found, key=lambda x: (x[0], x[1]))]


# --------------------------------------------------------------------------- pipeline

TOTAL = 8
LOCK = threading.Lock()
ARM_STEP = {"boundary": 5, "page": 6, "work": 7}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for the protocol, and a boundary over it")
    world = setup(cli)

    banner(3, TOTAL, f"The trial's memory — {len(d['pages'])} protocol pages")
    store(cli, world)

    banner(4, TOTAL, "Can the index find the rule? — `search --top-k 1` for every question")
    found = find_all(cli, world)
    hits = found.pop("hits")

    rows: list[dict] = []
    tallies: dict = {}
    base: dict = {}
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the trial assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        key = "answer" if arm != "work" else "work"
        if arm != "page":
            emit("text", "\n  system prompt, as the app sends it on every call:\n"
                 + "\n".join("    │ " + x for x in d["system"][key].splitlines()))
        if arm == "boundary":
            note("first, each question once without a boundary: those prompt tokens are the prompt alone, so a "
                 "boundary call with the same count had nothing attached (∅)")
            base = baselines(cli)
        else:
            note(f"no boundary: the app appends “{d['page_header']}” and the page step 4 found for the question to "
                 "the system prompt" + (" (the same prompt as step 5)" if arm == "page" else ""))
        note(f"`router chat` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ✗ wrong · – “No record” "
             "although the protocol has it · ✎ answered what the protocol does not say"
             + (" · ∅ nothing attached" if arm == "boundary" else ""))
        got = ask_all(cli, world, arm, hits, base)
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: " + " · ".join(
            f"{d['kinds'][k]} {tally_line(tallies[arm], k)}" for k in KINDS))
        emit("tally", "", arm=arm, **tallies[arm])

    banner(8, TOTAL, "Scoreboard")
    if tallies:
        emit("text", f"  {'':<30} {'the rule itself':<22} {'applied to a participant':<26} not in the protocol")
        for arm in ARMS:
            if arm in tallies:
                t = tallies[arm]
                emit("text", f"  {d['arms'][arm]['short']:<30} {tally_line(t, 'rule'):<22} {tally_line(t, 'applied'):<26} "
                             f"{tally_line(t, 'none')}")
        p = tallies.get("page")
        if p:
            emit("text", f"\n  With the right page in the prompt, the rule itself: {p['rule']['right']}/{p['rule']['answers']}. "
                         f"The same rules applied to a participant: {p['applied']['right']}/{p['applied']['answers']}.")
        w = tallies.get("work")
        if w and w["applied"]["answers"]:
            emit("text", f"  Quote the rule, then work it: applied {w['applied']['right']}/{w['applied']['answers']}; the quoted "
                         f"rule was on the page word for word in {w['applied']['quoted']} of {w['applied']['answers']}.")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the study team instead of the protocol's answer (✗ wrong · ✎ invented · "
                     "– “No record” · ∅ nothing attached):\n"
             + "\n".join(said[:16]) + (f"\n    … {len(said) - 16} more in out/report.json" if len(said) > 16 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, found=found, baselines=base,
                   tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:16])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: the six protocol pages and the fifteen questions."""
    d = load_data()
    view = pages_view(d)
    for v in view:
        emit("text", f"\n  {v['file']} — {v['title']}\n      {v['first']}")
    emit("text", "\n  the questions:\n" + "\n".join(
        f"    {q['id']:<3} [{d['kinds'][q['kind']]}] {q['say']}\n         → {q['fact']}" for q in d["questions"]))
    out = dict(pages=view, questions=d["questions"])
    emit("protocol", "", **out)
    return out


def ask_one(cli: CLI, text: str) -> dict:
    """One question the third way: the page search finds, the rule word for word, the steps, the answer."""
    d = load_data()
    world = load_world(cli)
    say = " ".join(text.split())
    if not say:
        die("give a question, e.g. \"Participant 1042 weighed 93 kg this morning. What dose of CVT-118 do they get today?\"")
    cli.router_ok = True  # type: ignore[attr-defined]
    hit = search_page(cli, world["project"]["protocol"], say, echo=True)
    note(f"top page: {hit['file'] or 'nothing'}")
    got = chat(cli, say, with_page(d["system"]["work"], d["page_header"], hit), None, MAX_TOKENS["work"], echo=True)
    if not got:
        return {}
    reply = got[0]
    q = quote_check(reply, hit.get("text") or "")
    emit("text", "\n" + "\n".join("  " + ln for ln in reply.splitlines() if ln.strip()))
    emit("text", f"\n  quoted rule: {'word for word on ' + str(hit['file']) if q == 'verbatim' else q}")
    out = dict(say=say, file=hit["file"], reply=reply, quote=q, steps=steps_of(reply), answer=answer_line(reply))
    emit("one", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = n_proj = 0
    for name in PROJECTS:
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundary, {n_proj} project(s) ({n_docs} document(s)) and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


EXAMPLE = "Participant 1042 weighed 93 kg this morning. What dose of CVT-118 do they get today?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "protocol", "cleanup"],
                    help="run = full demo (default); ask \"<question>\" = one question the third way (the page, the "
                         "rule word for word, the steps, the answer); protocol = the six pages and the fifteen "
                         "questions (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "protocol":
        banner(1, 1, "Protocol — the six pages and the fifteen questions (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "ask":
        if not args.args:
            die(f"usage: python3 demo.py ask \"{EXAMPLE}\"")
        banner(2, 2, f"Ask — “{' '.join(args.args)}”: the page, the rule word for word, the steps")
        ask_one(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py ask \"Participant 1088 weighs 81 kg today. What dose of CVT-118 do they get?\"`; "
          "`python3 demo.py cleanup` removes the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        # The failing command can be `auth login --api-key …`: never print the key.
        shown_cmd = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-…", shlex.join(e.cmd)) if e.cmd else "(memorylake)"
        print(f"\ncommand failed: {shown_cmd}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

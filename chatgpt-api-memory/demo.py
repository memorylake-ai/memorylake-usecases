#!/usr/bin/env python3
"""
Add cross-session context to every ChatGPT API call — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
"Lingofox" is a language tutor built on the ChatGPT API.  Each call to the API
starts blank, so the tutor has never known who it is talking to.  Two learners
have used it for a month: Mina (Japanese, keigo, moving to Osaka) and Tomás
(German, two-way prepositions, football).  Every past session was written to
MemoryLake; each learner has one project, one actor and one *boundary* — a saved
scope of the memory a model may read.

Then the app keeps its chat call and adds one argument: the learner's boundary.
MemoryRouter (`memorylake router chat|messages`, OpenAI- and Anthropic-
compatible) retrieves the memories that matter for this message and puts them in
front of the model.  The demo measures what that costs in prompt tokens against
pasting the whole history, sends the same boundary to a Claude model, shows that
an off-topic message gets no memory at all, and pipes tonight's session back in
so the next call already knows what changed.

Every check is printed: which expected details the reply mentions (✓/✗), which of
the other learner's details leaked (should be none), and the prompt tokens the
provider billed.  Model wording varies run to run; the README reports totals over
all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance; the demo
uses the smallest models (about 10 calls, a few thousand tokens in total).
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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cgm"  # memorylake-usecases / chatgpt-api-memory

TUTOR = dict(custom_id=f"{PREFIX}-tutor", display="Lingofox tutor", role="language tutor bot built on the ChatGPT API")
CHAT_MODEL = "gpt-4o-mini"           # OpenAI Chat Completions through MemoryRouter
CLAUDE_MODEL = "claude-haiku-4-5"    # Anthropic Messages through MemoryRouter
SYSTEM = "You are Lingofox, a friendly language tutor. Keep answers under 150 words."
TODAY_PROMPT = "I've got 15 minutes tonight. Give me a practice plan."
OFF_TOPIC = "Quick one: what is 17 times 23?"


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
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "balance"}
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


# --------------------------------------------------------------------------- data

def load_learners() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "learners").glob("*.json"))]


def learner(key: str) -> dict:
    for l in load_learners():
        if l["key"] == key:
            return l
    die(f"unknown learner {key!r}; pick one of {', '.join(l['key'] for l in load_learners())}")
    return {}


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def check(text: str, checks: list[dict]) -> list[dict]:
    """Which expected details a reply mentions. A term is a substring; `re:` marks a regex (e.g. any Japanese script)."""
    low = (text or "").lower()

    def hit(w: str) -> bool:
        return bool(re.search(w[3:], low)) if w.startswith("re:") else w.lower() in low
    return [dict(label=c["label"], ok=any(hit(w) for w in c["any"])) for c in checks]


def leaked(text: str, other: dict) -> list[str]:
    low = (text or "").lower()
    return [w for w in other["only_mine"] if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)]


def marks(results: list[dict]) -> str:
    return "  ".join(f"{'✓' if r['ok'] else '✗'} {r['label']}" for r in results)


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, custom_id: str, display: str, description: str, tags: str) -> str:
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", display,
                        "--tags", tags, "--description", description)
        note(f"created actor {display} → {actor['id']}")
    else:
        note(f"actor {display} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def find_project(cli: CLI, key: str):
    return cli.try_run("proj", "get", cid(key, "sessions"), "--by-custom-id", scoped=True, echo=False)


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


def conv_ids_of(l: dict) -> list[str]:
    return [cid(s["custom_id"]) for s in l["sessions"]] + ([cid(l["today"]["custom_id"])] if l.get("today") else [])


def reset_learner(cli: CLI, l: dict) -> None:
    for c in conv_ids_of(l):
        conv = cli.try_run("conv", "get", c, "--by-custom-id", scoped=True, echo=False)
        if conv:
            cli.run("conv", "delete", conv["id"], scoped=True)
    proj = find_project(cli, l["key"])
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
    actor = cli.try_run("actor", "get", cid("learner", l["user_id"]), "--by-custom-id", echo=False)
    if actor:
        delete_actor(cli, actor["id"])
    for b in demo_boundaries(cli):
        if b.get("custom_id") == cid(l["key"], "boundary"):
            cli.run("boundary", "delete", b["id"])
    (OUT / f"writeback-{l['key']}.json").unlink(missing_ok=True)


def setup(cli: CLI, reset: bool) -> dict:
    if reset:
        note("--reset: deleting every learner's sessions, project, actor and boundary first")
        for l in load_learners():
            reset_learner(cli, l)
    tutor = ensure_actor(cli, TUTOR["custom_id"], TUTOR["display"], TUTOR["role"], "bot,demo")
    emit("tutor", "", id=tutor, display=TUTOR["display"])
    world = {"tutor": tutor, "learners": {}}
    existing = {b["custom_id"]: b for b in demo_boundaries(cli, echo=True)}
    for l in load_learners():
        actor = ensure_actor(cli, cid("learner", l["user_id"]), l["display"],
                             f"Lingofox learner {l['user_id']} ({l['language']})", "learner,demo")
        proj = find_project(cli, l["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", f"Lingofox — {l['display']}", "--custom-id", cid(l["key"], "sessions"),
                           "--description", f"Every tutoring session of learner {l['user_id']}. Demo data.", scoped=True)
            note(f"created project “{proj['name']}” → {proj['id']}")
        else:
            note(f"project “{proj['name']}” already exists → {proj['id']}")
        b = existing.get(cid(l["key"], "boundary"))
        if b is None:
            # The boundary is the whole integration: this learner's project and this learner's actor. Nothing
            # else is readable through it, so a model called with it cannot see another learner's memory.
            b = cli.run("boundary", "create", "--name", f"Lingofox — {l['display']}", "--custom-id", cid(l["key"], "boundary"),
                        "--projects", proj["id"], "--human-actor", actor, scoped=True)
            note(f"created boundary {b['id']}: project {proj['id']} + actor {actor}")
        else:
            note(f"boundary already exists → {b['id']}")
        world["learners"][l["key"]] = dict(actor=actor, project=proj["id"], boundary=b["id"])
        emit("learner", "", key=l["key"], display=l["display"], language=l["language"], actor=actor,
             project=proj["id"], boundary=b["id"])
    return world


def load_world(cli: CLI) -> dict:
    world = {"tutor": None, "learners": {}}
    bnds = {b["custom_id"]: b for b in demo_boundaries(cli)}
    for l in load_learners():
        actor = cli.try_run("actor", "get", cid("learner", l["user_id"]), "--by-custom-id", echo=False)
        proj = find_project(cli, l["key"])
        b = bnds.get(cid(l["key"], "boundary"))
        if not (actor and proj and b):
            die("the demo learners do not exist yet; run `python3 demo.py` first")
        world["learners"][l["key"]] = dict(actor=actor["id"], project=proj["id"], boundary=b["id"])
    return world


# --------------------------------------------------------------------------- step 3: past sessions

def append_session(cli: CLI, l: dict, s: dict, w: dict, tutor: str) -> tuple[str, int]:
    """Store one session as a DIRECT conversation; returns (conversation id, messages appended now)."""
    custom = cid(s["custom_id"])
    conv = cli.try_run("conv", "get", custom, "--by-custom-id", scoped=True)
    done = 0
    if conv is not None and w["project"] not in (conv.get("rw_project_ids") or []):
        cli.run("conv", "delete", conv["id"], scoped=True)
        conv = None
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", custom, "--project", w["project"], "--actors", f"{w['actor']},{tutor}",
                       "--kind", "DIRECT", "--name", f"{l['display']} — {s['title']}", "--metadata", f"user_id={l['user_id']}",
                       scoped=True)
    else:
        done = len((cli.run("conv", "msg", "list", conv["id"], "--page-size", "50") or {}).get("items") or [])
    emit("session", "", learner=l["key"], custom_id=s["custom_id"], id=conv["id"], title=s["title"], date=s["date"],
         turns=len(s["turns"]), done=done)
    start = datetime.fromisoformat(s["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    parent = conv.get("current_message_id")
    for i, (who, text) in enumerate(s["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(seconds=40 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", w["actor"] if who == "user" else tutor,
                "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", text]
        if parent:
            args += ["--parent", parent]
        msg = cli.run(*args, scoped=True, echo=i == done + 1)
        parent = msg["id"]
        emit("turn", "", learner=l["key"], custom_id=s["custom_id"], index=i, who=who)
    appended = len(s["turns"]) - done
    if appended and done == 0:
        note(f"{appended} messages stored for “{l['display']} — {s['title']}” ({s['date'][:10]}) (the first append is echoed; the rest are the same command)")
    elif appended:
        note(f"resumed “{s['title']}”: {appended} more message(s)")
    else:
        note(f"“{l['display']} — {s['title']}” already stored ({done} messages)")
    return conv["id"], appended


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for c in sorted(pending):
            try:
                status = cli.run("conv", "cook-status", c, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                continue
            first = False
            if status.get("cook_finished"):
                pending.discard(c)
                emit("cooked", "", id=c)
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} session(s) still being turned into memory ({int(time.time() - t0)}s)")
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} session(s) still processing server-side")
    else:
        note(f"memory ready in {int(time.time() - t0)}s")


def list_facts(cli: CLI, scope: str, ident: str, echo: bool = True) -> list[dict]:
    out, token = [], None
    while True:
        args = ["fact", "list", scope, ident, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def past_sessions(cli: CLI, world: dict) -> None:
    convs, fresh = [], set()
    for l in load_learners():
        w = world["learners"][l["key"]]
        for s in l["sessions"]:
            c, appended = append_session(cli, l, s, w, world["tutor"])
            convs.append(c)
            if appended:
                fresh.add(l["key"])
    wait_for_memory(cli, convs)
    for l in load_learners():
        if l["key"] not in fresh:
            continue
        # Extraction wording varies run to run; the details the tutor must never lose are also written as facts,
        # verbatim, on the learner. (Only when this run stored the sessions: a resume does not pin twice.)
        have = {f["fact"] for f in list_facts(cli, "--actors", world["learners"][l["key"]]["actor"], echo=False)}
        todo = [p for p in l["pins"] if p not in have]
        if todo:
            cli.run("fact", "add", "--actor", world["learners"][l["key"]]["actor"], *todo, scoped=True)
            note(f"pinned {len(todo)} key detail(s) for {l['display']} with `fact add`")


# --------------------------------------------------------------------------- step 4: what each boundary holds

def boundary_memory(cli: CLI, world: dict, echo: bool = True) -> dict:
    out = {}
    for l in load_learners():
        w = world["learners"][l["key"]]
        on_actor = list_facts(cli, "--actors", w["actor"], echo=echo)
        on_proj = list_facts(cli, "--projects", w["project"], echo=echo)
        lines = [f"\n  {l['display']} — boundary {w['boundary']}: {len(on_actor)} fact(s) on the learner, "
                 f"{len(on_proj)} on the sessions project"]
        lines += [f"   [learner] {textwrap.shorten(f['fact'], 96)}" for f in on_actor[:8]]
        if len(on_actor) > 8:
            lines.append(f"   … {len(on_actor) - 8} more on the learner")
        lines += [f"   [project] {textwrap.shorten(f['fact'], 96)}" for f in on_proj[:4]]
        if len(on_proj) > 4:
            lines.append(f"   … {len(on_proj) - 4} more on the project")
        emit("text", "\n".join(lines))
        out[l["key"]] = dict(actor=[f["fact"] for f in on_actor], project=[f["fact"] for f in on_proj])
        emit("memory", "", learner=l["key"], actor=out[l["key"]]["actor"], project=out[l["key"]]["project"])
    return out


# --------------------------------------------------------------------------- MemoryRouter

def protocol_for(model: str) -> str:
    return "messages" if model.startswith("claude") else "chat"


def router(cli: CLI, prompt: str, model: str = CHAT_MODEL, boundary: str | None = None, body: Path | None = None,
           label: str = "", max_tokens: int = 350) -> dict | None:
    """One model call through MemoryRouter. Returns {text, prompt_tokens, completion_tokens} or None if unavailable."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    proto = protocol_for(model)
    args = ["router", proto, prompt, "--model", model, "--max-tokens", str(max_tokens), "--temperature", "0"]
    if body is None:
        args += ["--system", SYSTEM]
    else:
        args += ["--body", os.path.relpath(body)]
    if boundary:
        args += ["--boundary", boundary]
    try:
        res = cli.run(*args)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("router steps below are skipped; the memory steps need no balance. Top up in the console to see them.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    if proto == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = res["usage"]["prompt_tokens"], res["usage"]["completion_tokens"]
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        u = res.get("usage") or {}
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct, model=res.get("model", model), label=label,
                boundary=boundary)


def show_reply(who: str, r: dict, width: int = 76, max_lines: int = 9) -> None:
    lines = []
    for para in r["reply"].splitlines():
        lines += textwrap.wrap(para, width - 6) or [""]
    lines = [x for i, x in enumerate(lines) if x or (i and lines[i - 1])]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    emit("text", f"\n  {who}  ·  prompt {r['prompt_tokens']} tokens · reply {r['completion_tokens']}\n"
                 + "\n".join("    │ " + x for x in lines))


# --------------------------------------------------------------------------- step 5: the same call, with and without memory

def same_call(cli: CLI, world: dict) -> dict:
    learners = load_learners()
    emit("text", f"\n  Today's message, from either learner: “{TODAY_PROMPT}”\n"
                 f"  The app's call is the same every time; only `--boundary` changes.")
    res = {}
    r = router(cli, TODAY_PROMPT, label="no memory")
    if r is None:
        return res
    show_reply("No boundary (the plain ChatGPT API call)", r)
    r["checks"] = {l["key"]: check(r["reply"], l["checks"]) for l in learners}
    for l in learners:
        note(f"mentions {l['display']}'s details: {marks(r['checks'][l['key']])}")
    res["none"] = r
    emit("call", "", slot="none", **r)
    for l in learners:
        other = next(o for o in learners if o["key"] != l["key"])
        r = router(cli, TODAY_PROMPT, boundary=world["learners"][l["key"]]["boundary"], label=l["display"])
        if r is None:
            return res
        show_reply(f"Boundary of {l['display']}", r)
        r["checks"] = {l["key"]: check(r["reply"], l["checks"])}
        r["leaks"] = leaked(r["reply"], other)
        note(f"{l['display']}'s details: {marks(r['checks'][l['key']])}")
        note(f"{other['display']}'s details in this reply: {', '.join(r['leaks']) if r['leaks'] else 'none'}"
             + ("  ✓ isolated" if not r["leaks"] else "  ✗ LEAK"))
        res[l["key"]] = r
        emit("call", "", slot=l["key"], **r)
    base = res["none"]["prompt_tokens"]
    emit("text", "\n  Prompt tokens billed for the same message:\n"
         + f"    {'no boundary':<26}{base:>6}\n"
         + "\n".join(f"    {'boundary of ' + l['display'].split()[0]:<26}{res[l['key']]['prompt_tokens']:>6}"
                     f"   (+{res[l['key']]['prompt_tokens'] - base} = the memories MemoryRouter attached)" for l in learners if l["key"] in res))
    return res


# --------------------------------------------------------------------------- step 6: the token bill

def history_body(l: dict) -> Path:
    """The usual workaround: paste every past session into the system prompt."""
    OUT.mkdir(exist_ok=True)
    lines = [SYSTEM, "", f"Here is the full history of your past sessions with {l['display'].split()[0]}:"]
    for s in l["sessions"]:
        lines += ["", f"--- Session “{s['title']}” on {s['date'][:10]} ---"]
        lines += [f"{'Learner' if who == 'user' else 'Tutor'}: {text}" for who, text in s["turns"]]
    p = OUT / f"history-{l['key']}.json"
    p.write_text(json.dumps({"messages": [{"role": "system", "content": "\n".join(lines)}]}, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def token_bill(cli: CLI, world: dict, calls: dict) -> dict:
    l = load_learners()[0]
    w = world["learners"][l["key"]]
    out = {}
    body = history_body(l)
    note(f"{body.relative_to(HERE)}: {len(l['sessions'])} sessions, "
         f"{sum(len(s['turns']) for s in l['sessions'])} messages pasted into the system prompt")
    r = router(cli, TODAY_PROMPT, body=body, label="pasted history")
    if r is None:
        return out
    show_reply(f"No boundary, {l['display'].split()[0]}'s whole history pasted in", r, max_lines=5)
    r["checks"] = {l["key"]: check(r["reply"], l["checks"])}
    note(f"{l['display']}'s details: {marks(r['checks'][l['key']])}")
    out["history"] = r
    emit("call", "", slot="history", **r)
    mem = calls.get(l["key"])
    off_plain = router(cli, OFF_TOPIC, label="off-topic, no memory", max_tokens=40)
    off_mem = router(cli, OFF_TOPIC, boundary=w["boundary"], label="off-topic, boundary", max_tokens=40)
    if not (off_plain and off_mem):
        return out
    out["off_plain"], out["off_mem"] = off_plain, off_mem
    emit("call", "", slot="off_plain", **off_plain)
    emit("call", "", slot="off_mem", **off_mem)
    base = calls["none"]["prompt_tokens"] if "none" in calls else 0
    rows = [("pasted history (3 sessions)", r["prompt_tokens"], r["prompt_tokens"] - base),
            ("boundary (MemoryRouter)", mem["prompt_tokens"], mem["prompt_tokens"] - base)] if mem else []
    emit("text", f"\n  “{TODAY_PROMPT}” for {l['display']} — what the context cost:\n"
         + "".join(f"    {name:<30}{pt:>6} prompt tokens   (context: {ctx})\n" for name, pt, ctx in rows)
         + (f"    → the boundary sent {int(100 * (rows[1][2]) / max(rows[0][2], 1) + 0.5)}% of the pasted history's context, "
            f"and the history only grows with every session.\n" if rows else "")
         + f"\n  “{OFF_TOPIC}” — nothing in memory is relevant:\n"
         + f"    {'no boundary':<30}{off_plain['prompt_tokens']:>6} prompt tokens   → {textwrap.shorten(off_plain['reply'], 40)}\n"
         + f"    {'boundary of ' + l['display'].split()[0]:<30}{off_mem['prompt_tokens']:>6} prompt tokens   → {textwrap.shorten(off_mem['reply'], 40)}")
    if off_mem["prompt_tokens"] == off_plain["prompt_tokens"]:
        note("same prompt size with and without the boundary: MemoryRouter attached nothing to an off-topic message")
    else:
        note(f"the boundary added {off_mem['prompt_tokens'] - off_plain['prompt_tokens']} tokens to an off-topic message")
    emit("bill", "", rows=[dict(name=n, prompt=p, context=c) for n, p, c in rows],
         off=[off_plain["prompt_tokens"], off_mem["prompt_tokens"]])
    return out


# --------------------------------------------------------------------------- step 7: switch models

def switch_models(cli: CLI, world: dict) -> dict | None:
    l = load_learners()[0]
    other = load_learners()[1]
    note(f"same boundary, Anthropic Messages protocol, {CLAUDE_MODEL} — the memory lives outside the model")
    r = router(cli, TODAY_PROMPT, model=CLAUDE_MODEL, boundary=world["learners"][l["key"]]["boundary"], label=CLAUDE_MODEL)
    if r is None:
        return None
    show_reply(f"{CLAUDE_MODEL} · boundary of {l['display']}", r)
    r["checks"] = {l["key"]: check(r["reply"], l["checks"])}
    r["leaks"] = leaked(r["reply"], other)
    note(f"{l['display']}'s details: {marks(r['checks'][l['key']])}")
    note(f"{other['display']}'s details: {', '.join(r['leaks']) if r['leaks'] else 'none  ✓ isolated'}")
    emit("call", "", slot="claude", **r)
    return r


# --------------------------------------------------------------------------- step 8: the next session

def next_session(cli: CLI, world: dict) -> dict:
    l = load_learners()[0]
    t = l["today"]
    w = world["learners"][l["key"]]
    state_file = OUT / f"writeback-{l['key']}.json"
    custom = cid(t["custom_id"])
    stored = cli.try_run("conv", "get", custom, "--by-custom-id", scoped=True, echo=False)
    before = None
    if stored is None:
        emit("text", f"\n  Before tonight's session is stored, the app asks: “{t['followup']}”")
        before = router(cli, t["followup"], boundary=w["boundary"], label="before")
        if before is None:
            return {}
        show_reply(f"Boundary of {l['display']} — before", before, max_lines=5)
        before["checks"] = check(before["reply"], t["before"])
        note(f"answers from what it knew: {marks(before['checks'])}")
        OUT.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
    elif state_file.exists():
        before = json.loads(state_file.read_text(encoding="utf-8"))
        note(f"tonight's session was stored by an earlier run; its 'before' answer is replayed from {state_file.relative_to(HERE)}")
        show_reply(f"Boundary of {l['display']} — before (earlier run)", before, max_lines=5)
    else:
        note("tonight's session was stored by an earlier run and its 'before' answer is gone; `--reset` replays it")
    if before:
        emit("call", "", slot="before", **before)
    emit("text", f"\n  Tonight's session, piped in exactly like the earlier ones (the app writes every turn):")
    for who, text in t["turns"]:
        emit("text", f"    {'Mina ' if who == 'user' else 'Tutor'}: {text}")
    c, appended = append_session(cli, l, t, w, world["tutor"])
    if appended:
        wait_for_memory(cli, [c])
    facts = list_facts(cli, "--actors", w["actor"], echo=True) + list_facts(cli, "--projects", w["project"], echo=False)
    # No \b: extracted facts can be Japanese, where "N3" sits right next to a kana or kanji ("N3の教材").
    new = [f["fact"] for f in facts if re.search(r"(?<![A-Za-z0-9])[NＮ][3３](?![0-9])", f["fact"])]
    emit("text", "\n  What MemoryLake extracted from tonight (facts mentioning N3):\n"
         + ("\n".join(f"   - {textwrap.shorten(x, 110)}" for x in new) or "   (none)"))
    if not new:
        note("no fact names N3 this time (extraction wording varies); the next call shows whether the change got through")
    emit("text", f"\n  Same question, next call: “{t['followup']}”")
    after = router(cli, t["followup"], boundary=w["boundary"], label="after")
    if after is None:
        return {"before": before}
    show_reply(f"Boundary of {l['display']} — after", after, max_lines=5)
    after["checks"] = check(after["reply"], t["after"])
    note(f"picks up tonight's session: {marks(after['checks'])}")
    emit("call", "", slot="after", **after)
    emit("writeback", "", facts=new)
    return {"before": before, "after": after, "facts": new}


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        out, token = [], None
        while True:
            args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=False) or {}
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

    try:
        found = facts()
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):
            try:
                found = facts()
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        cli.run("fact", "delete", "--actor", actor_id, f["id"], scoped=True, echo=False)
    left = facts()
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id)
    if found:
        note(f"deleted {len(found)} fact(s) on {actor_id} before the actor")
    return len(found)


def cleanup(cli: CLI) -> None:
    for b in demo_boundaries(cli, echo=True):
        cli.run("boundary", "delete", b["id"])
        note(f"deleted boundary {b['name']}")
    for l in load_learners():
        for c in conv_ids_of(l):
            conv = cli.try_run("conv", "get", c, "--by-custom-id", scoped=True, echo=False)
            if conv:
                cli.run("conv", "delete", conv["id"], scoped=True)
        proj = find_project(cli, l["key"])
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted {l['display']}'s sessions and project")
        actor = cli.try_run("actor", "get", cid("learner", l["user_id"]), "--by-custom-id")
        if actor:
            delete_actor(cli, actor["id"])
            note(f"deleted learner {l['display']} and every fact on them")
        (OUT / f"writeback-{l['key']}.json").unlink(missing_ok=True)
    tutor = cli.try_run("actor", "get", TUTOR["custom_id"], "--by-custom-id")
    if tutor:
        delete_actor(cli, tutor["id"])
        note("deleted the tutor actor")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    banner(2, TOTAL, "Set up — per learner: a project, an actor, and a boundary over both")
    world = setup(cli, reset)

    banner(3, TOTAL, "Past sessions — every chat the app had is stored as a conversation")
    past_sessions(cli, world)

    banner(4, TOTAL, "What each boundary holds — the memory a model may read for that learner")
    boundary_memory(cli, world)

    banner(5, TOTAL, "Same API call, three ways — no memory, Mina's boundary, Tomás's boundary")
    calls = same_call(cli, world)

    banner(6, TOTAL, "The token bill — pasted history vs. a boundary, and an off-topic message")
    bill = token_bill(cli, world, calls) if calls else {}

    banner(7, TOTAL, "Switch models — the same boundary through the Anthropic Messages protocol")
    claude = switch_models(cli, world) if calls else None

    banner(8, TOTAL, "The next session — tonight's chat is piped in; the next call already knows")
    wb = next_session(cli, world)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(calls=calls, bill=bill, claude=claude, writeback=wb, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']} prompt + {tok['completion']} reply tokens, "
                 f"billed to your MemoryLake balance.\n  Full replies and checks: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok)  # type: ignore[attr-defined]
    return summary


def ask(cli: CLI, prompt: str, who: str, model: str) -> None:
    world = load_world(cli)
    boundary = None if who == "none" else world["learners"][learner(who)["key"]]["boundary"]
    r = router(cli, prompt, model=model, boundary=boundary, label=who)
    if r is None:
        return
    show_reply(f"{model} · {'no boundary' if boundary is None else 'boundary of ' + learner(who)['display']}", r, max_lines=14)
    for l in load_learners():
        note(f"{l['display']}'s details: {marks(check(r['text'], l['checks']))}")
    emit("ask", "", who=who, model=model, prompt=prompt, **r)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "memory", "cleanup"],
                    help="run = full demo (default); ask = one router call; memory = what each boundary holds; cleanup = delete everything")
    ap.add_argument("prompt", nargs="?", help="for `ask`: the message to send")
    ap.add_argument("--learner", default="mina", help="for `ask`: mina, tomas, or none (no boundary)")
    ap.add_argument("--model", default=CHAT_MODEL, help=f"for `ask`: any model `memorylake router models` lists (default {CHAT_MODEL})")
    ap.add_argument("--reset", action="store_true", help="delete the demo learners, sessions and boundaries first and replay everything")
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
    if args.command == "memory":
        banner(2, 2, "What each boundary holds")
        boundary_memory(cli, load_world(cli))
        return
    if args.command == "ask":
        if not args.prompt:
            die('usage: python3 demo.py ask "your message" [--learner mina|tomas|none] [--model claude-haiku-4-5]')
        banner(2, 2, "One call through MemoryRouter")
        ask(cli, args.prompt, args.learner, args.model)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py ask "Quiz me on something" --learner tomas`; `python3 demo.py cleanup` removes the demo data.')


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

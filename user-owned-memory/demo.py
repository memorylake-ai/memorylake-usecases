#!/usr/bin/env python3
"""
User-owned memory for an embedded agent: whose memory is this? — a MemoryLake
demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Sprout (fictional) is a wellness-coach app with an AI coach inside it.  Six
users (Omar, Dana, Priya, Tom, Lena, Sam) have told the coach things about
themselves: allergies, injuries, schedules, goals.  The coach answers through
MemoryRouter: the app passes a boundary and MemoryRouter attaches the memories
that matter for the message.

The same 30 memories are stored three ways:

  · pooled   — one project for every user, the owner in `metadata.user_id`
               (how an app built on a vector store with a metadata filter
               usually keeps them)
  · labeled  — the same pool, with the owner's name written into each fact
  · own      — one actor per user, one boundary per user over that actor

On 2026-10-15 each user asks the coach one question.

What it shows
-------------
  · `search` returns each hit's metadata, so an app reading search results can
    filter by user.  MemoryRouter takes a boundary, not a filter, and attaches
    fact text only: the owner does not travel with the memory.  Omar asks about
    food allergies and is told about Dana's peanut allergy.
  · Writing the owner's name into the fact does not fix it: the name is right
    there in what was attached, and the model still tells Sam to mind his left
    knee (Dana's sprain).
  · A boundary per user does: each question sees only that user's memory, and
    the user owns it — export is `fact list --actors <them>`, delete is deleting
    the facts on their actor.  In the pool, both mean scanning everyone's facts.

Model wording varies run to run; every reply is graded and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (18 calls
on gpt-4o-mini and 19 on claude-haiku-4-5 per run).
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-uom"  # memorylake-usecases / user-owned-memory


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


MODEL = dict(protocol="chat", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Chat Completions)")
CHECKER = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")

STORES = {
    "pooled": dict(label="Pooled · owner in metadata", tag="one project for every user; metadata.user_id says whose fact it is"),
    "labeled": dict(label="Pooled · owner named in the text", tag="the same pool; each fact starts with the user's name"),
    "own": dict(label="Each user's own scope", tag="one actor per user, one boundary per user over that actor"),
}
ORDER = ["pooled", "labeled", "own"]

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). If you were given none, write NONE inside the backticks. "
                "Then answer the question.")


# --------------------------------------------------------------------------- data

def load_sprout() -> dict:
    return json.loads((DATA / "sprout.json").read_text(encoding="utf-8"))


def users_by_key(sp: dict) -> dict[str, dict]:
    return {u["key"]: u for u in sp["users"]}


def labeled_text(user: dict, fact: dict) -> str:
    return f"{user['name']} (user {user['key']}): {fact['text']}"


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(".")


def mark(ok) -> str:
    return "·" if ok is None else ("✓" if ok else "✗")


def owner_of(line: str, sp: dict) -> str | None:
    """Whose fact a copied-out memory line is (word for word against what the demo stored, ignoring case/spacing)."""
    n = norm(line)
    for u in sp["users"]:
        for f in u["facts"]:
            if n in (norm(f["text"]), norm(labeled_text(u, f))):
                return u["key"]
    return None


def foreign(reply: str, sp: dict, asker: str) -> dict[str, list[str]]:
    """Details in the reply that belong to another user: their name, or a detail only they have."""
    low = (reply or "").lower()
    hits: dict[str, list[str]] = {}
    for u in sp["users"]:
        if u["key"] == asker:
            continue
        found = [u["name"]] if re.search(r"\b" + re.escape(u["name"].lower()) + r"\b", low) else []
        for f in u["facts"]:
            found += [m for m in f["marks"] if re.search(r"(?<![a-z0-9])" + re.escape(m), low)]
        if found:
            hits[u["key"]] = sorted(set(found), key=found.index)
    return hits


def grade(reply: str, q: dict, sp: dict) -> dict:
    other = foreign(reply, sp, q["user"])
    expect_ok = bool(re.search(q["expect"], reply or "", re.I)) if q["expect"] else True
    return dict(ok=not other and expect_ok, foreign=other, expect_ok=expect_ok)


def whose(other: dict[str, list[str]], sp: dict) -> str:
    names = users_by_key(sp)
    return ", ".join(f"{names[k]['name']}'s ({', '.join(v)})" for k, v in other.items())


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, store: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(store), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid("user", key), "--by-custom-id", echo=echo)


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


def list_facts(cli: CLI, project: str | None = None, actor: str | None = None, echo: bool = True) -> list[dict]:
    """Every fact in one scope, page by page until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--page-size", "200"]
        args += ["--projects", project] if project else ["--actors", actor]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def leftovers(cli: CLI) -> bool:
    if demo_boundaries(cli) or any(find_project(cli, s) for s in ("pooled", "labeled")):
        return True
    return any(find_actor(cli, u["key"]) for u in load_sprout()["users"])


def setup(cli: CLI) -> dict:
    sp = load_sprout()
    if leftovers(cli):
        note("an earlier run left Sprout's memory behind; starting over (the story begins before anyone asks)")
        cleanup(cli, quiet=True)
    world: dict = dict(stores={}, users={})
    for store in ("pooled", "labeled"):
        first = store == "pooled"
        proj = cli.run("proj", "create", "--name", f"Sprout user memory ({store})", "--custom-id", cid(store),
                       "--description", f"Sprout coach memory, {STORES[store]['tag']}. Demo data.", scoped=True, echo=first)
        b = cli.run("boundary", "create", "--name", f"Sprout coach ({store})", "--custom-id", cid("b", store),
                    "--projects", proj["id"], scoped=True, echo=first)
        world["stores"][store] = dict(project=proj["id"], boundary=b["id"])
        note(f"{STORES[store]['label']}: project {proj['id']}, boundary {b['id']}"
             + ("" if first else " (same commands)"))
    for i, u in enumerate(sp["users"]):
        first = i == 0
        actor = cli.run("actor", "create", "--custom-id", cid("user", u["key"]), "--display-name", u["name"],
                        "--description", f"Sprout app user {u['key']}. Demo data.", echo=first)
        try:
            cli.run("actor", "bind", "--actor", actor["id"], scoped=True, echo=first)
        except CLIError as e:
            if not e.has("409", "already"):
                raise
        b = cli.run("boundary", "create", "--name", f"Sprout coach for {u['name']}", "--custom-id",
                    cid("b", "user", u["key"]), "--human-actor", actor["id"], scoped=True, echo=first)
        world["users"][u["key"]] = dict(actor=actor["id"], boundary=b["id"])
        if first:
            note("the same three commands for the other five users")
    emit("text", "\n    " + "  ".join(f"{u['name']} → {world['users'][u['key']]['boundary']}" for u in sp["users"][:3])
         + "  …")
    note(f"{STORES['own']['label']}: {len(sp['users'])} actors, {len(sp['users'])} boundaries (`--human-actor`)")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    sp = load_sprout()
    bs = {b["custom_id"]: b["id"] for b in demo_boundaries(cli)}
    world: dict = dict(stores={}, users={})
    for store in ("pooled", "labeled"):
        proj = find_project(cli, store)
        if not proj or cid("b", store) not in bs:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["stores"][store] = dict(project=proj["id"], boundary=bs[cid("b", store)])
    for u in sp["users"]:
        a = find_actor(cli, u["key"])
        if not a or cid("b", "user", u["key"]) not in bs:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["users"][u["key"]] = dict(actor=a["id"], boundary=bs[cid("b", "user", u["key"])])
    return world


# --------------------------------------------------------------------------- step 3: the memories, three ways

def set_meta(cli: CLI, fact_id: str, project: str, meta: dict, echo: bool = False) -> None:
    """`fact update` is idempotent (same metadata set again), so a dropped connection is retried."""
    args = ["fact", "update", fact_id, "--project", project, "--metadata", json.dumps(meta, ensure_ascii=False)]
    for attempt in range(5):
        try:
            cli.run(*args, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def add_facts(cli: CLI, texts: list[str], project: str | None = None, actor: str | None = None,
              echo: bool = True) -> list[str]:
    """`fact add` with many texts in one call; ids come back in the same order. Not idempotent: after a network
    error, add only what is still missing and map texts to ids from the list."""
    scope = ["--project", project] if project else ["--actor", actor]
    if echo:
        shown = ["fact", "add", *scope, *texts[:2]]
        more = f"  … {len(texts) - 2} more" if len(texts) > 2 else ""
        emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace]) + more)
    try:
        res = cli.run("fact", "add", *scope, *texts, scoped=True, echo=False)
        return [f["id"] for f in res["facts"]]
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        have = {f["fact"] for f in list_facts(cli, project, actor, echo=False)}
        missing = [t for t in texts if t not in have]
        note(f"`fact add` hit a network error; adding the {len(missing)} fact(s) still missing")
        if missing:
            cli.run("fact", "add", *scope, *missing, scoped=True, echo=False)
        by_text = {f["fact"]: f["id"] for f in list_facts(cli, project, actor, echo=False)}
        return [by_text[t] for t in texts]


def store(cli: CLI, world: dict) -> None:
    sp = load_sprout()
    pairs = [(u, f) for u in sp["users"] for f in u["facts"]]
    for st in ("pooled", "labeled"):
        proj = world["stores"][st]["project"]
        texts = [f["text"] if st == "pooled" else labeled_text(u, f) for u, f in pairs]
        ids = add_facts(cli, texts, project=proj, echo=st == "pooled")
        metas = [dict(user_id=u["key"], app=sp["app"]) for u, _ in pairs]
        if st == "pooled":
            emit("cmd", shlex.join(["memorylake", "fact", "update", ids[0], "--project", proj, "--metadata",
                                    json.dumps(metas[0]), "--workspace", cli.workspace]) + "  … one per fact")
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda im: set_meta(cli, im[0], proj, im[1]), zip(ids, metas)))
        facts = list_facts(cli, project=proj, echo=st == "pooled")
        if sorted(f["fact"] for f in facts) != sorted(texts):
            die(f"the {st} project holds {len(facts)} facts, expected {len(texts)}")
        note(f"{STORES[st]['label']}: {len(facts)} facts in one project"
             + (", each with metadata.user_id" if st == "pooled" else
                ", the same text with the owner's name in front, same metadata"))
    for i, u in enumerate(sp["users"]):
        a = world["users"][u["key"]]["actor"]
        add_facts(cli, [f["text"] for f in u["facts"]], actor=a, echo=i == 0)
        n = len(list_facts(cli, actor=a, echo=False))
        if n != len(u["facts"]):
            die(f"{u['name']}'s actor holds {n} facts, expected {len(u['facts'])}")
    note(f"{STORES['own']['label']}: {len(sp['users'])} × {len(sp['users'][0]['facts'])} facts, each on its owner's actor")
    d = users_by_key(sp)["dana"]
    emit("text", "\n  One of Dana's memories, three ways:\n"
         f"    pooled   {d['facts'][0]['text']:<62} metadata.user_id = \"dana\"\n"
         f"    labeled  {labeled_text(d, d['facts'][0])}\n"
         f"    own      {d['facts'][0]['text']:<62} on actor Dana")
    emit("memory", "", users=len(sp["users"]), per_user=len(sp["users"][0]["facts"]), total=len(pairs))


# --------------------------------------------------------------------------- step 4: the metadata is there

def show_search(cli: CLI, world: dict) -> list[dict]:
    sp = load_sprout()
    q = "food allergies"
    res = cli.run("search", q, "--projects", world["stores"]["pooled"]["project"], "--types", "fact", "--top-k", "5",
                  scoped=True) or {}
    hits = res.get("facts") or res.get("items") or []
    names = users_by_key(sp)
    rows = []
    lines = []
    for h in hits:
        uid = (h.get("metadata") or {}).get("user_id")
        rows.append(dict(fact=h.get("fact"), user_id=uid))
        lines.append(f"    #{h.get('rank', len(rows))}  {textwrap.shorten(h.get('fact') or '', 52):<54}"
                     f"metadata.user_id = {json.dumps(uid)}  ({names[uid]['name'] if uid in names else '?'})")
    emit("text", "\n".join(lines))
    keep = [r for r in rows if r["user_id"] == "omar"]
    note(f"every hit carries its owner, so an app that calls `search` for Omar can keep only user_id = \"omar\": "
         f"{len(keep)} of {len(rows)} hit(s) here")
    note("MemoryRouter takes a boundary, not a filter: what it attaches is the fact text")
    emit("search", "", query=q, hits=rows)
    return rows
# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, body: Path, boundary: str | None = None, tool: dict = MODEL,
           max_tokens: int = 200, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. `body` holds the system message (and any conversation so far)."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = [tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", str(max_tokens),
            "--temperature", "0", "--body", str(body.relative_to(HERE))]
    if boundary:
        args += ["--boundary", boundary]
    t0 = time.time()
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
    secs = time.time() - t0
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    if tool["protocol"] == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct, seconds=round(secs, 1))


def write_body(name: str, system: str, tool: dict = MODEL) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{name}.json"
    if tool["protocol"] == "chat":
        body = {"model": tool["model"], "messages": [{"role": "system", "content": system}]}
    else:
        body = {"model": tool["model"], "system": system, "messages": []}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return path



def boundary_for(world: dict, st: str, user: str) -> str:
    return world["users"][user]["boundary"] if st == "own" else world["stores"][st]["boundary"]


def bodies(sp: dict) -> dict[str, Path]:
    """One request body per user (the app's system prompt names who is talking). Written once, before any thread."""
    return {u["key"]: write_body(f"coach-{u['key']}", sp["system"].format(name=u["name"])) for u in sp["users"]}


def ask_all(cli: CLI, world: dict, quiet: bool = False) -> list[dict]:
    """Every user's question through each of the three stores: `router chat --boundary`. One question at a time,
    its three stores in parallel, so each result is shown as it comes in."""
    sp = load_sprout()
    names = users_by_key(sp)
    body = bodies(sp)
    rows = []
    for n, q in enumerate(sp["questions"]):
        def one(st, echo=False):
            return st, router(cli, q["say"], body[q["user"]], boundary=boundary_for(world, st, q["user"]), echo=echo)

        if n == 0 and not quiet:
            got = [one(ORDER[0], echo=True)]  # alone, so its command is echoed before the others start
            note("every other call is the same command with that user's body and boundary, three stores at a time")
            with ThreadPoolExecutor(3) as pool:
                got += list(pool.map(one, ORDER[1:]))
        else:
            with ThreadPoolExecutor(3) as pool:
                got = list(pool.map(one, ORDER))
        out = [f"\n    {names[q['user']]['name']}: {q['say']}"]
        for st, r in got:
            reply = r["reply"] if r else ""
            g = grade(reply, q, sp) if r else dict(ok=None, foreign={}, expect_ok=None)
            row = dict(user=q["user"], say=q["say"], store=st, reply=reply, **g)
            rows.append(row)
            tail = (f"   ← {whose(row['foreign'], sp)}" if row["foreign"] else
                    (f"   ← missed: {q['expect_label']}" if row["ok"] is False else ""))
            out.append(f"      {st:<8} {mark(row['ok'])} {textwrap.shorten(reply, 84)}{tail}")
            emit("answer", "", **row)
        if not quiet:
            emit("text", "\n".join(out))
    if any(r["ok"] is not None for r in rows) and not quiet:
        parts = []
        for st in ORDER:
            got_st = [r for r in rows if r["store"] == st and r["ok"] is not None]
            parts.append(f"{st} {sum(r['ok'] for r in got_st)}/{len(got_st)}")
        used = {st: sum(1 for r in rows if r["store"] == st and r["foreign"]) for st in ORDER}
        emit("text", "\n    right: " + " · ".join(parts)
             + "\n    answers that used another user's memory: "
             + " · ".join(f"{st} {used[st]}" for st in ORDER))
    return rows


# --------------------------------------------------------------------------- step 6: what MemoryRouter attached

def attached(cli: CLI, boundary: str, say: str) -> list[str] | None:
    """Ask Claude the same question through the same boundary, told (in the system prompt) to copy out first every
    memory MemoryRouter attached. The user message is unchanged, so retrieval sees the same query."""
    body = OUT / "body-checker.json"
    r = router(cli, say, body, boundary=boundary, tool=CHECKER, max_tokens=1500, echo=False)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    return [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]


def inspect_all(cli: CLI, world: dict) -> list[dict]:
    sp = load_sprout()
    names = users_by_key(sp)
    if not cli.router_ok:  # type: ignore[attr-defined]
        note("skipped: needs MemoryRouter")
        return []
    write_body("checker", CHECK_SYSTEM, tool=CHECKER)
    checks = []
    for q in sp["questions"]:
        with ThreadPoolExecutor(3) as pool:
            got = list(pool.map(lambda st: (st, attached(cli, boundary_for(world, st, q["user"]), q["say"])), ORDER))
        out = [f"\n    {names[q['user']]['name']}: {q['say']}"]
        for st, lines in got:
            if lines is None:
                continue
            owners = [owner_of(ln, sp) for ln in lines]
            theirs = [(o, ln) for o, ln in zip(owners, lines) if o != q["user"]]
            row = dict(user=q["user"], store=st, lines=lines, owners=owners,
                       own=sum(1 for o in owners if o == q["user"]), other=len(theirs))
            checks.append(row)
            out.append(f"      {st:<8} {len(lines)} attached: {row['own']} {names[q['user']]['name']}'s, "
                       f"{row['other']} someone else's")
            for o, ln in theirs:
                who = names[o]["name"] if o else "? not a stored fact"
                out.append(f"                 [{who}] {textwrap.shorten(ln, 80)}")
            emit("inspect", "", **row)
        emit("text", "\n".join(out))
    if checks:
        per = {st: [c for c in checks if c["store"] == st] for st in ORDER}
        note("lines attached that belong to someone else: "
             + " · ".join(f"{st} {sum(c['other'] for c in per[st])} (on {sum(1 for c in per[st] if c['other'])}"
                          f"/{len(per[st])} questions)" for st in ORDER))
        note("in the labeled pool the owner's name is in every attached line, and the model still used them; "
             "only the per-user boundary keeps other people's memories out of the prompt")
        note("the checker is its own call, so what it was given can differ a little from what the answer above got; "
             "over many runs the pattern holds (README, Measured)")
    return checks


# --------------------------------------------------------------------------- step 7: the user owns it

def export_user(cli: CLI, world: dict, key: str, echo: bool = True) -> dict:
    sp = load_sprout()
    u = users_by_key(sp)[key]
    facts = list_facts(cli, actor=world["users"][key]["actor"], echo=echo)
    doc = dict(app=sp["app"], user=key, name=u["name"], actor=world["users"][key]["actor"],
               exported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               facts=[dict(id=f["id"], text=f["fact"], created_at=f.get("created_at")) for f in facts])
    OUT.mkdir(exist_ok=True)
    path = OUT / f"export-{key}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(path=str(path.relative_to(HERE)), facts=[f["fact"] for f in facts])


def pooled_records(cli: CLI, world: dict, key: str) -> dict[str, dict]:
    """In the two pools, finding one user's facts means reading everyone's and filtering."""
    sp = load_sprout()
    u = users_by_key(sp)[key]
    out = {}
    for st in ("pooled", "labeled"):
        facts = list_facts(cli, project=world["stores"][st]["project"], echo=st == "pooled")
        if st == "pooled":
            mine = [f for f in facts if (f.get("metadata") or {}).get("user_id") == key]
        else:
            mine = [f for f in facts if f["fact"].startswith(f"{u['name']} (user {key}):")]
        out[st] = dict(read=len(facts), mine=mine)
    return out


def delete_scoped(cli: CLI, fact_id: str, scope: list[str]) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def ownership(cli: CLI, world: dict) -> dict:
    sp = load_sprout()
    names = users_by_key(sp)
    emit("text", "\n  Omar asks for a copy of everything the coach remembers about him:")
    ex = export_user(cli, world, "omar")
    emit("text", f"    {len(ex['facts'])} facts → {ex['path']}\n"
         + "\n".join(f"      - {t}" for t in ex["facts"]))
    emit("export", "", user="omar", **ex)

    emit("text", "\n  Dana closes her account and asks Sprout to forget her:")
    dana = world["users"]["dana"]
    facts = list_facts(cli, actor=dana["actor"], echo=False)
    if facts:
        emit("cmd", shlex.join(["memorylake", "fact", "delete", facts[0]["id"], "--actor", dana["actor"],
                                "--workspace", cli.workspace]) + f"  … one per fact ({len(facts)})")
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: delete_scoped(cli, f["id"], ["--actor", dana["actor"]]), facts))
    left = facts
    for attempt in range(4):
        left = list_facts(cli, actor=dana["actor"], echo=attempt == 0)
        if not left:
            break
        for f in left:
            delete_scoped(cli, f["id"], ["--actor", dana["actor"]])
        time.sleep(2)
    if left:
        die(f"{len(left)} of Dana's facts are still on her actor after deleting")
    note(f"deleted {len(facts)} facts on Dana's actor; `fact list --actors` now returns 0")
    q = next(q for q in sp["questions"] if q["user"] == "dana")
    lines = attached(cli, dana["boundary"], q["say"]) if cli.router_ok else None  # type: ignore[attr-defined]
    if lines is not None:
        stored = [ln for ln in lines if owner_of(ln, sp)]
        emit("text", f"    her boundary, same question (“{q['say']}”): {len(stored)} stored fact(s) attached"
             + (f" (and {len(lines) - len(stored)} line(s) that are not stored facts)" if len(lines) > len(stored) else ""))
    pooled = pooled_records(cli, world, "dana")
    for st in ("pooled", "labeled"):
        p = pooled[st]
        how = "metadata.user_id" if st == "pooled" else "the name in the text"
        emit("text", f"    {st:<8} read all {p['read']} facts to find hers by {how}: {len(p['mine'])} found")
    emit("text", "    until those are found and deleted, they are still attached to other users' questions "
         "(step 6: Dana's peanut allergy, knee sprain, physio)")
    for st in ("pooled", "labeled"):
        proj = world["stores"][st]["project"]
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: delete_scoped(cli, f["id"], ["--project", proj]), pooled[st]["mine"]))
    note(f"deleted them as well ({len(pooled['pooled']['mine'])} + {len(pooled['labeled']['mine'])}); "
         "in the pool that took a scan of every user's memory, here it took Dana's own scope")
    row = dict(export=ex, dana_deleted=len(facts), dana_attached_after=lines,
               pooled_scan={st: dict(read=pooled[st]["read"], found=len(pooled[st]["mine"])) for st in pooled})
    emit("ownership", "", **row)
    return row


# --------------------------------------------------------------------------- scoreboard

def scoreboard(rows: list[dict], checks: list[dict]) -> dict:
    sp = load_sprout()
    names = users_by_key(sp)
    lines = [f"\n    {'':<30}{'pooled':>10}{'labeled':>10}{'own':>8}"]
    cell = {(r["user"], r["store"]): r for r in rows}
    for q in sp["questions"]:
        lab = f"{names[q['user']]['name']}: {q['expect_label']}"
        lines.append(f"    {textwrap.shorten(lab, 28):<30}"
                     + "".join(f"{mark(cell[(q['user'], st)]['ok']):>{10 if st != 'own' else 8}}" for st in ORDER))
    n = len(sp["questions"])
    tot = {st: sum(1 for r in rows if r["store"] == st and r["ok"]) for st in ORDER}
    used = {st: sum(1 for r in rows if r["store"] == st and r["foreign"]) for st in ORDER}
    other = {st: sum(c["other"] for c in checks if c["store"] == st) for st in ORDER}
    lines.append(f"    {'right':<30}" + "".join(f"{str(tot[st]) + '/' + str(n):>{10 if st != 'own' else 8}}" for st in ORDER))
    lines.append(f"    {'used someone else`s memory':<30}"
                 + "".join(f"{used[st]:>{10 if st != 'own' else 8}}" for st in ORDER))
    if checks:
        lines.append(f"    {'others` lines attached':<30}"
                     + "".join(f"{other[st]:>{10 if st != 'own' else 8}}" for st in ORDER))
    emit("text", "\n".join(lines).replace("`", "'"))
    emit("scoreboard", "", totals=tot, used=used, other_lines=other, questions=n)
    return dict(right=tot, used=used, other_lines=other)


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    sp = load_sprout()
    banner(2, TOTAL, "Set up — two pooled projects, and per user: an actor and a boundary")
    world = setup(cli)

    banner(3, TOTAL, f"What {len(sp['users'])} Sprout users told the coach — the same 30 memories, stored three ways")
    store(cli, world)

    banner(4, TOTAL, "The owner is in the metadata — `search` returns it")
    hits = show_search(cli, world)
    time.sleep(5)

    banner(5, TOTAL, f"{sp['today']} — each user asks the coach one question, through each store")
    note("`router chat --boundary` (gpt-4o-mini, temperature 0); the system prompt names who is talking")
    rows = ask_all(cli, world)

    banner(6, TOTAL, "Why — what MemoryRouter attached (Claude copies out what sits above the question)")
    note("the same 18 calls to claude-haiku-4-5, told in the system prompt to copy out its memory first")
    checks = inspect_all(cli, world)

    banner(7, TOTAL, "The user owns it — Omar exports his memory, Dana deletes hers")
    own = ownership(cli, world)

    banner(8, TOTAL, "Scoreboard")
    tot = scoreboard(rows, checks)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(answers=rows, checks=checks, search=hits, ownership=own, totals=tot, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every question, answer and attached line: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], totals=tot)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def ask_one(cli: CLI, user: str, say: str, stores: list[str]) -> list[dict]:
    """One user's question through the chosen stores, as the memory stands now."""
    sp = load_sprout()
    names = users_by_key(sp)
    if user not in names:
        die(f"unknown user {user!r}; one of: {', '.join(names)}")
    world = load_world(cli)
    body = bodies(sp)[user]
    q = dict(user=user, say=say, expect="")
    rows = []
    for st in stores:
        r = router(cli, say, body, boundary=boundary_for(world, st, user), echo=st == stores[0])
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        g = grade(r["reply"], q, sp)
        tail = f"   ← {whose(g['foreign'], sp)}" if g["foreign"] else ""
        emit("text", f"    {st:<8} {r['reply']}{tail}")
        rows.append(dict(store=st, reply=r["reply"], foreign=g["foreign"]))
    return rows


def show_memory(cli: CLI, user: str) -> dict:
    """What each store holds about one user, and what it takes to find it."""
    sp = load_sprout()
    names = users_by_key(sp)
    if user not in names:
        die(f"unknown user {user!r}; one of: {', '.join(names)}")
    world = load_world(cli)
    pooled = pooled_records(cli, world, user)
    own = list_facts(cli, actor=world["users"][user]["actor"])
    out = {}
    for st in ("pooled", "labeled"):
        p = pooled[st]
        emit("text", f"\n  {STORES[st]['label']}: read {p['read']} facts, {len(p['mine'])} are {names[user]['name']}'s")
        for f in p["mine"]:
            emit("text", f"    - {f['fact']}")
        out[st] = [f["fact"] for f in p["mine"]]
    emit("text", f"\n  {STORES['own']['label']}: `fact list --actors` → {len(own)} facts")
    for f in own:
        emit("text", f"    - {f['fact']}")
    out["own"] = [f["fact"] for f in own]
    return out


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    try:
        found = list_facts(cli, actor=actor_id, echo=False)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):
            try:
                found = list_facts(cli, actor=actor_id, echo=False)
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        delete_scoped(cli, f["id"], ["--actor", actor_id])
    left = list_facts(cli, actor=actor_id, echo=False)
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    if bs and not quiet:
        note(f"deleted {len(bs)} boundaries")
    for st in ("pooled", "labeled"):
        proj = find_project(cli, st, echo=not quiet)
        if not proj:
            continue
        facts = list_facts(cli, project=proj["id"], echo=False)
        left = facts
        for attempt in range(4):  # a list right after many deletes can still show a few: delete those and look again
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_scoped(cli, f["id"], ["--project", proj["id"]]), left))
            time.sleep(2 * attempt)
            left = list_facts(cli, project=proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in the {st} project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=False)
        if not quiet:
            note(f"deleted the {st} project and its {len(facts)} fact(s)")
    n_actors = n_facts = 0
    for u in load_sprout()["users"]:
        a = find_actor(cli, u["key"], echo=not quiet and n_actors == 0)
        if a:
            n_facts += delete_actor(cli, a["id"])
            n_actors += 1
    if n_actors and not quiet:
        note(f"deleted {n_actors} user actors and the {n_facts} fact(s) on them (facts first: "
             "`actor delete` alone would leave them undeletable)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "memory", "cleanup"],
                    help="run = full demo (default); ask <user> \"<question>\" = one question through each store as "
                         "the memory stands now; memory <user> = what each store holds about a user; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: <user> \"<question>\"; for memory: <user>")
    ap.add_argument("--store", choices=ORDER, action="append", help="for ask: only this store (repeatable)")
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
    if args.command == "memory":
        if len(args.args) != 1:
            die("usage: python3 demo.py memory <user>")
        banner(2, 2, f"What each store holds about {args.args[0]}")
        show_memory(cli, args.args[0].lower())
        return
    if args.command == "ask":
        if len(args.args) != 2:
            die('usage: python3 demo.py ask <user> "<question>" [--store pooled|labeled|own]')
        banner(2, 2, "One question to the coach, through each store, as the memory stands now")
        ask_one(cli, args.args[0].lower(), args.args[1], args.store or ORDER)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py memory omar` or `python3 demo.py ask sam "Can I do overhead presses?"`; '
          '`python3 demo.py cleanup` removes the demo data.')


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

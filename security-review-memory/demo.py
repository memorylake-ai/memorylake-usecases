#!/usr/bin/env python3
"""
Answering an enterprise security review with live evidence — a MemoryLake demo
driven entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Quillstack (fictional) sells an agent that drafts procurement memos, and keeps
each customer's memory in MemoryLake. Halden Mutual (a fictional bank) is
evaluating it, and its vendor-risk team sent a seven-question security
questionnaire about that memory: credentials, rotation, least privilege,
revocation, temporary access, audit trail, offboarding.

Instead of writing "yes" in a spreadsheet, the demo answers every question with
a check run against your own MemoryLake team, right now:

- three integration keys are issued (`api-key create`; one with `--expires-at`),
  each logged into its own isolated CLI profile; the secret is printed once
  and a retry with the same idempotency key does not print it again
- Halden's tenant memory is written **through the ingest worker's key**
- that key is rotated (`api-key rotate`): same id, new secret, the old one is
  refused at once, and every fact is still there
- the same key is checked for what it can reach: `caller_role`, another
  customer's memory, the team's key list (it is team-wide — the answer is no)
- the retired support console's key cannot revoke itself (409); the owner
  revokes it, it is refused, and what it wrote stays
- the contractor's key is refused right after its expiry time
- `fact trace` shows what changed, when and from which message — but not which
  key made the change
- Halden is offboarded: `proj delete` → `fact list` 404, `search` 400

Every answer goes into an evidence pack (out/security-review-halden.md and .json)
with the verdict, the commands and what they returned — including the answers
that are "no". Keys are only ever shown as `sk-` + 4 characters. The demo only
creates, rotates and revokes keys named `mlu-sec-…`, never the key it runs with.

The companion web app (web/server.py) drives the same functions and streams the
same events. Requirements: Python 3.9+, the memorylake CLI on PATH, and a
MemoryLake API key.
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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
KEYS_DIR = STATE_DIR / "keys"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-sec"  # memorylake-usecases / security-review-memory

REFUSAL_WAIT = 30    # seconds a revoked / rotated / expired key is polled until the server refuses it
COOK_WAIT = 300      # upper bound for the conversation to be processed (usually 30–90 s)

SECRET_RE = re.compile(r"sk-([A-Za-z0-9]{4})[A-Za-z0-9_-]{8,}")


def mask(s: str) -> str:
    """Every API key that could reach the screen is cut to sk- + 4 characters."""
    return SECRET_RE.sub(lambda m: f"sk-{m.group(1)}…", s or "")


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
_EMIT_LOCK = threading.Lock()


def emit(kind: str, text: str = "", **data) -> None:
    with _EMIT_LOCK:
        EMIT(kind, mask(text), data)


def banner(step: int, total: int, title: str) -> None:
    emit("step", f"Step {step}/{total}  {title}", index=step, total=total, title=title)


def note(msg: str) -> None:
    emit("note", msg)


def die(msg: str) -> None:
    raise DemoError(msg)


# --------------------------------------------------------------------------- CLI wrapper

class CLIError(RuntimeError):
    def __init__(self, cmd: list[str], rc: int, stdout: str, stderr: str):
        super().__init__(mask(stderr.strip() or stdout.strip() or f"exit {rc}"))
        self.cmd, self.rc, self.stdout, self.stderr = cmd, rc, stdout, stderr

    def has(self, *needles: str) -> bool:
        blob = (self.stderr + self.stdout).lower()
        return any(n.lower() in blob for n in needles)


class CLI:
    """Runs `memorylake …` under one profile, echoes the command, parses the JSON reply, retries read-only calls.

    `who` names the credential the command runs with: "owner" (the key you started the demo with) or one of the
    integration keys, which each live in their own isolated profile under .memorylake-demo/keys/<name>/."""

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "search", "trace"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out", "error sending request",
                 "connection closed", "temporarily unavailable", "http 502", "http 503", "http 504")

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False, who: str = "owner"):
        self.binary, self.env, self.show_json, self.who = binary, env, show_json, who
        self.workspace: str | None = None
        self.base_url = DEFAULT_BASE_URL

    def _echo(self, args: list[str]) -> None:
        line = shlex.join(["memorylake", *args])
        if self.who != "owner":
            line += f"   # as {self.who}"
        emit("cmd", line, who=self.who)

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

    def attempt(self, *args: str, scoped: bool = False, echo: bool = True) -> dict:
        """One call whose failure is an expected answer (401, 404, 409, 400): rc, the server's message, the JSON."""
        rc, out, err = self.raw(*args, scoped=scoped, echo=echo)
        msg = server_message(err or out) if rc else ""
        body = None
        if rc == 0 and out.strip():
            try:
                body = json.loads(out)
            except json.JSONDecodeError:
                body = out
        return dict(rc=rc, status=http_status(err) if rc else 200, message=msg, body=body)


def server_message(text: str) -> str:
    """The server's own sentence from a CLI error, without the request id."""
    lines = [ln.strip() for ln in mask(text).splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("{") or ln.startswith("Error:") or ln.startswith("Caused by") or ln.startswith("HTTP "):
            continue
        return re.sub(r"\s*\(request id: [^)]*\)", "", ln)
    return lines[0] if lines else ""


def http_status(text: str) -> int:
    m = re.search(r"HTTP (\d{3})", text or "")
    return int(m.group(1)) if m else 0


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
    cli.own_prefix = None  # type: ignore[attr-defined]
    if api_key:
        STATE_DIR.mkdir(exist_ok=True)
        env["MEMORYLAKE_CONFIG_DIR"] = str(STATE_DIR)
        note(f"API key given — logging into an isolated profile under {STATE_DIR.name}/")
        cli.run("auth", "login", "--api-key", api_key, "--base-url", base_url, "--profile", PROFILE)
        m = re.match(r"sk-([A-Za-z0-9]{8})", api_key)
        cli.own_prefix = m.group(1) if m else None  # type: ignore[attr-defined]
    else:
        note("no API key given — using your existing `memorylake auth login` session")
        rc, out, _ = cli.raw("auth", "status")
        if rc != 0 or "Logged in: yes" not in out:
            die("not logged in. Either export MEMORYLAKE_API_KEY=sk-… or run `memorylake auth login` first.")
        if base_url == DEFAULT_BASE_URL:
            m = re.search(r"(https://\S+/openapi/memorylake)", out)
            cli.base_url = m.group(1) if m else base_url
    team = cli.run("team", "get")
    note(f"connected to team “{team.get('name')}” ({team.get('type')}) as {team.get('caller_role')}")
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


# --------------------------------------------------------------------------- data + helpers

def load_review() -> dict:
    return json.loads((DATA / "review.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def now() -> int:
    return int(time.time())


def iso(ts: int | float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _pages(cli: CLI, args: list[str], echo: bool = True, scoped: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, "--page-size", "100", *(["--continuation-token", token] if token else []),
                       scoped=scoped, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def _get(cli: CLI, *args: str, scoped: bool = False, echo: bool = True):
    try:
        return cli.run(*args, scoped=scoped, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_project(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "proj", "get", cid(slug), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "actor", "get", cid(slug), "--by-custom-id", echo=echo)


def find_conv(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "conv", "get", cid(slug), "--by-custom-id", scoped=True, echo=echo)


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", "--projects", project_id], echo)


def demo_keys(cli: CLI, echo: bool = True) -> list[dict]:
    """Keys this demo created (name starts with the demo prefix) — never the key the demo runs with."""
    items = (cli.run("api-key", "list", echo=echo) or {}).get("items") or []
    own = getattr(cli, "own_prefix", None)
    return [k for k in items if str(k.get("name", "")).startswith(PREFIX + "-") and k.get("key_prefix") != own]


# --------------------------------------------------------------------------- integration keys (one isolated profile each)

def key_dir(name: str) -> Path:
    return KEYS_DIR / name


def key_cli(owner: CLI, name: str) -> CLI:
    env = {k: v for k, v in owner.env.items() if k != "MEMORYLAKE_API_KEY"}
    env["MEMORYLAKE_CONFIG_DIR"] = str(key_dir(name))
    c = CLI(owner.binary, env, owner.show_json, who=name)
    c.workspace, c.base_url = owner.workspace, owner.base_url
    return c


def login_key(owner: CLI, name: str, secret: str) -> CLI:
    """Store an integration key in its own profile; from here on the secret only lives in that profile's file."""
    d = key_dir(name)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    c = key_cli(owner, name)
    c.run("auth", "login", "--api-key", secret, "--base-url", owner.base_url, "--profile", PROFILE)
    return c


def forget_key(name: str) -> None:
    shutil.rmtree(key_dir(name), ignore_errors=True)


def until_refused(c: CLI, args: list[str], scoped: bool = True) -> dict:
    """Call with a key that should no longer work until the server refuses it (≤ REFUSAL_WAIT s)."""
    t0, first = time.time(), True
    while True:
        r = c.attempt(*args, scoped=scoped, echo=first)
        first = False
        r["after_s"] = round(time.time() - t0, 1)
        if r["rc"] != 0 or time.time() - t0 > REFUSAL_WAIT:
            r["refused"] = r["rc"] != 0
            return r
        time.sleep(1)


# --------------------------------------------------------------------------- evidence

class Pack:
    """The answers, one per question, as they are established."""

    def __init__(self, review: dict) -> None:
        self.review = review
        self.answers: dict[str, dict] = {}

    def answer(self, qid: str, verdict: str, answer: str, evidence: list[dict], note_: str = "",
               expected: str = "yes") -> dict:
        q = next(q for q in self.review["questions"] if q["id"] == qid)
        if verdict != expected:
            # the wording below is what this run was expected to show; say plainly that it did not
            answer = f"Not confirmed by this run — see the evidence (expected: {expected}). " + answer
        a = dict(id=qid, topic=q["topic"], question=q["text"], verdict=verdict, answer=answer, evidence=evidence,
                 note=note_)
        self.answers[qid] = a
        sym = {"yes": "✓ yes", "no": "✗ no", "partial": "◐ partly"}[verdict]
        emit("text", f"\n  {qid} {q['topic']}: {sym}\n    {answer}" + (f"\n    note: {note_}" if note_ else ""))
        emit("answer", "", **a)
        return a


def ev(cmd: str, result: str, ok: bool | None = None) -> dict:
    return dict(cmd=mask(cmd), result=mask(result), ok=ok)


def write_pack(pack: Pack, meta: dict) -> dict:
    r = pack.review
    qs = [pack.answers.get(q["id"]) for q in r["questions"]]
    counts = {v: sum(1 for a in qs if a and a["verdict"] == v) for v in ("yes", "partial", "no")}
    doc = dict(vendor=r["vendor"]["name"], buyer=r["buyer"]["name"], generated_at=meta["when"], team=meta["team"],
               workspace=meta["workspace"], base_url=meta["base_url"], counts=counts, answers=[a for a in qs if a])
    OUT.mkdir(exist_ok=True)
    (OUT / "security-review-halden.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sym = {"yes": "✓ Yes", "no": "✗ No", "partial": "◐ Partly"}
    lines = [f"# {r['vendor']['name']} — security review answers for {r['buyer']['name']}", "",
             f"Generated {meta['when']} by running each check against MemoryLake ({meta['base_url']}), "
             f"team “{meta['team']}”, workspace {meta['workspace']}. Keys appear as `sk-` + 4 characters.", "",
             f"**{counts['yes']} yes · {counts['partial']} partly · {counts['no']} no**", "",
             "| # | Topic | Answer |", "|---|---|---|"]
    for a in doc["answers"]:
        lines.append(f"| {a['id']} | {a['topic']} | {sym[a['verdict']]} |")
    for a in doc["answers"]:
        lines += ["", f"## {a['id']} · {a['topic']} — {sym[a['verdict']]}", "", f"> {a['question']}", "", a["answer"]]
        if a["note"]:
            lines += ["", f"*Note:* {a['note']}"]
        lines += ["", "Evidence:", ""]
        for e in a["evidence"]:
            mark_ = "" if e["ok"] is None else ("✓ " if e["ok"] else "✗ ")
            lines.append(f"- {mark_}`{e['cmd']}` → {e['result']}")
    (OUT / "security-review-halden.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return doc


# --------------------------------------------------------------------------- step 2: issue keys

def leftovers(cli: CLI) -> bool:
    """Anything from an earlier, interrupted run? Keys are one-shot, so every run starts from nothing."""
    r = load_review()
    left = demo_keys(cli, echo=False)
    projs = [s for s in r["tenants"] if find_project(cli, s, echo=False)]
    return bool(left or projs or KEYS_DIR.exists() and any(KEYS_DIR.iterdir()))


def issue_keys(cli: CLI, review: dict, pack: Pack, run_id: str) -> dict:
    keys: dict[str, dict] = {}
    note("one key per integration, each with a name that says what it is for")
    for k in review["keys"]:
        name = cid(k["slug"])
        args = ["api-key", "create", "--name", name, "--idempotency-key", f"{name}-{run_id}"]
        if k.get("expires_in_seconds"):
            args += ["--expires-at", str(now() + int(k["expires_in_seconds"]))]
        made = cli.run(*args)
        secret = made.get("key") or ""
        if not secret:
            die(f"`api-key create` returned no key for {name}")
        login_key(cli, name, secret)
        keys[k["slug"]] = dict(id=str(made["id"]), name=name, prefix=made.get("key_prefix"), purpose=k["purpose"],
                               fate=k["fate"], shown=mask(secret), length=len(secret))
        note(f"{name} → id {made['id']} · {mask(secret)} ({len(secret)} chars), stored in its own profile "
             f".memorylake-demo/keys/{name}/ and nowhere else")
        del secret
    ing = keys["ingest-worker"]
    note("retry the first create with the same idempotency key (what a client does after a timeout)")
    again = cli.run("api-key", "create", "--name", ing["name"], "--idempotency-key", f"{ing['name']}-{run_id}")
    replay_same = str(again.get("id")) == ing["id"]
    replay_secret = bool((again or {}).get("key"))
    note(f"replayed: id {again.get('id')} ({'same key' if replay_same else 'a NEW key'}), "
         f"{'the secret was printed again' if replay_secret else 'the secret field is empty — it is shown exactly once'}")
    listed = demo_keys(cli)
    got = cli.run("api-key", "get", ing["id"])
    leak = [k["name"] for k in listed if k.get("key")] + (["api-key get"] if (got or {}).get("key") else [])
    con = keys["contractor-export"]
    for k in listed:
        if str(k["id"]) == con["id"]:
            con["expires_at"] = k.get("expires_at")
    emit("text", "\n  " + f"{'id':<7}{'name':<28}{'prefix':<10}{'status':<9}expires_at\n  " + "\n  ".join(
        f"{k['id']:<7}{k['name']:<28}{k.get('key_prefix', ''):<10}{k.get('status', ''):<9}{iso(k.get('expires_at'))}"
        for k in listed))
    note(f"`api-key list` / `get` return {'no secret' if not leak else 'a secret for ' + ', '.join(leak)} — "
         f"only `key_prefix`")
    check = key_cli(cli, con["name"]).attempt("proj", "list", scoped=True)
    con["worked_before"] = check["rc"] == 0
    note(f"the contractor key works now ({'rc 0' if check['rc'] == 0 else check['message']}); "
         f"it expires at {iso(con.get('expires_at'))}")
    ok = replay_same and not replay_secret and not leak and len(keys) == 3
    pack.answer("Q1", "yes" if ok else "no",
                "Yes. Each integration gets its own named key (`api-key create --name`). The secret is in the create "
                "reply only: an idempotent retry returns the same key id without it, and `api-key list` / `get` "
                "return only an 8-character prefix.",
                [ev(f"api-key create --name {k['name']}", f"id {k['id']}, secret {k['shown']} ({k['length']} chars)", True)
                 for k in keys.values()]
                + [ev(f"api-key create --name {ing['name']} --idempotency-key <same>",
                      f"id {again.get('id')}, " + ("secret printed again" if replay_secret else "secret empty: shown only in the first reply"),
                      replay_same and not replay_secret),
                   ev("api-key list / api-key get", "fields: " + ", ".join(sorted(listed[0].keys())) if listed else "empty",
                      not leak)])
    emit("keys", "", keys=keys, replay=dict(id=again.get("id"), same=replay_same, secret=replay_secret),
         listed=[{x: k.get(x) for x in ("id", "name", "key_prefix", "status", "expires_at")} for k in listed])
    return keys


# --------------------------------------------------------------------------- step 3: tenant memory through the ingest key

def tenant_memory(cli: CLI, review: dict, keys: dict) -> dict:
    w = key_cli(cli, keys["ingest-worker"]["name"])
    t = review["tenants"]
    note("everything in this step runs with the ingest worker's key, not yours")
    proj = w.run("proj", "create", "--name", t["halden"]["name"], "--custom-id", cid("halden"),
                 "--description", t["halden"]["description"], scoped=True)
    ids = dict(halden=proj["id"])
    for p in ("ines", "quill"):
        person = review["people"][p]
        a = w.run("actor", "create", "--custom-id", cid(person["slug"]), "--display-name", person["name"],
                  "--description", person["description"])
        w.run("actor", "bind", "--actor", a["id"], scoped=True)
        ids[p] = a["id"]
    conv_spec = review["conversation"]
    conv = w.run("conv", "create", "--project", ids["halden"], "--name", conv_spec["name"], "--kind", "DIRECT",
                 "--actors", f"{ids['ines']},{ids['quill']}", "--custom-id", cid(conv_spec["slug"]), scoped=True)
    ids["conv"] = conv["id"]
    start = datetime.fromisoformat(conv_spec["start"].replace("Z", "+00:00"))
    n = len(conv_spec["messages"])
    t0 = time.time()
    for i, (who, text) in enumerate(conv_spec["messages"], 1):
        ts = start.replace(minute=start.minute + 2 * (i - 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", ids["conv"], "--actor", ids[who], "--custom-id", f"{cid('m')}{i}",
                "--timestamp", ts, "--text", text]
        if i == n:
            args += ["--wait", "--timeout", str(COOK_WAIT)]
        w.run(*args, scoped=True, echo=i in (1, n))
        if 1 < i < n:
            note(f"appended message {i}/{n}")
    note(f"the conversation was read into memory in {int(time.time() - t0)}s")
    pinned = {}
    for p in review["pinned"]:
        f = (w.run("fact", "add", "--project", ids["halden"], p["text"], scoped=True) or {}).get("facts") or []
        if not f:
            die("fact add returned no fact")
        fid = f[0]["id"]
        w.run("fact", "update", fid, "--project", ids["halden"], "--metadata",
              json.dumps({"written_by": keys["ingest-worker"]["name"], "kind": p["key"]}), scoped=True)
        pinned[p["key"]] = fid
        time.sleep(1)
    ids["pinned"] = pinned
    facts = list_facts(w, ids["halden"])
    rows = [dict(id=f["id"], text=f["fact"], pinned=f["id"] in pinned.values()) for f in facts]
    emit("text", "\n  Halden Mutual's memory, written by the ingest worker:\n  " + "\n  ".join(
        f"{'[pinned]   ' if r['pinned'] else '[extracted]'} {clip(r['text'], 100)}" for r in rows))
    # another customer, written by the owner, so step 5 can ask whether the ingest key reaches it
    o = t["orrin"]
    orrin = cli.run("proj", "create", "--name", o["name"], "--custom-id", cid("orrin"), "--description", o["description"],
                    scoped=True)
    ids["orrin"] = orrin["id"]
    cli.run("fact", "add", "--project", orrin["id"], o["fact"], scoped=True)
    note("a second customer, Orrin Freight, has its own project — written with your key")
    emit("tenant", "", ids=ids, facts=rows, conversation=dict(id=ids["conv"], messages=n),
         orrin=dict(id=orrin["id"], name=o["name"]))
    return ids


# --------------------------------------------------------------------------- step 4: rotate

def rotate_step(cli: CLI, keys: dict, ids: dict, pack: Pack, run_id: str) -> dict:
    k = keys["ingest-worker"]
    before = sorted(f["id"] for f in list_facts(cli, ids["halden"]))
    note(f"{len(before)} fact(s) in Halden's memory before the rotation")
    rot = cli.run("api-key", "rotate", k["id"], "--idempotency-key", f"{k['name']}-rotate-{run_id}")
    secret = rot.get("key") or ""
    if not secret:
        die("`api-key rotate` returned no key")
    new_name = k["name"] + "-v2"
    shown = mask(secret)
    note(f"same id {rot.get('id')}, new prefix {rot.get('key_prefix')} (was {k['prefix']}), new secret {shown}")
    old = key_cli(cli, k["name"])
    refused = until_refused(old, ["fact", "list", "--projects", ids["halden"]])
    note(f"old secret: {'refused' if refused['refused'] else 'STILL ACCEPTED'} after {refused['after_s']}s"
         + (f" — HTTP {refused['status']} “{refused['message']}”" if refused["refused"] else ""))
    nw = login_key(cli, new_name, secret)
    del secret
    after = sorted(f["id"] for f in list_facts(nw, ids["halden"]))
    same = after == before
    note(f"new secret: rc 0, {len(after)} fact(s) — {'the same ids' if same else 'DIFFERENT ids'}")
    forget_key(k["name"])
    k.update(rotated_to=new_name, new_prefix=rot.get("key_prefix"), new_shown=shown)
    ok = str(rot.get("id")) == k["id"] and rot.get("key_prefix") != k["prefix"] and refused["refused"] and same
    pack.answer("Q2", "yes" if ok else "no",
                "Yes. `api-key rotate` keeps the key id and issues a new secret; the old secret was refused on the very "
                f"next call ({refused['after_s']} s later), and the new one read the same {len(after)} facts — nothing "
                "is re-ingested, because memory belongs to the team, not to the key.",
                [ev(f"api-key rotate {k['id']}", f"same id, prefix {k['prefix']} → {rot.get('key_prefix')}",
                    str(rot.get("id")) == k["id"]),
                 ev("fact list (old secret)", f"HTTP {refused['status']} {refused['message']}" if refused["refused"]
                    else "accepted", refused["refused"]),
                 ev("fact list (new secret)", f"{len(after)} facts, same ids as before: {same}", same)])
    out = dict(id=k["id"], old_prefix=k["prefix"], new_prefix=rot.get("key_prefix"), shown=shown, refused=refused,
               before=len(before), after=len(after), same=same)
    emit("rotate", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: what one key can reach

def scope_step(cli: CLI, keys: dict, ids: dict, review: dict, pack: Pack) -> dict:
    w = key_cli(cli, keys["ingest-worker"]["rotated_to"])
    note("the ingest worker only needs Halden's project. What else does its key reach?")
    team = w.run("team", "get")
    role = team.get("caller_role")
    note(f"caller_role: {role}")
    projs = [p for p in _pages(w, ["proj", "list"]) if str(p.get("custom_id", "")).startswith(PREFIX + "-")]
    sees_orrin = any(p["id"] == ids["orrin"] for p in projs)
    orrin_facts = w.attempt("fact", "list", "--projects", ids["orrin"], scoped=True)
    read_orrin = [f.get("fact") for f in ((orrin_facts.get("body") or {}).get("items") or [])]
    note(f"Orrin Freight's memory: {'readable — ' + clip(read_orrin[0], 90) if read_orrin else 'refused: ' + orrin_facts['message']}")
    keylist = w.attempt("api-key", "list")
    n_keys = len(((keylist.get("body") or {}).get("items")) or [])
    note(f"`api-key list` with the ingest key: {'rc 0, ' + str(n_keys) + ' keys of the team (prefixes)' if keylist['rc'] == 0 else keylist['message']}")
    scoped = role not in ("tenant_owner", "owner", "admin") and not read_orrin and keylist["rc"] != 0
    pack.answer("Q3", "yes" if scoped else "no",
                "No — not per key. A MemoryLake API key acts for the whole team: the ingest worker's key reports "
                f"`caller_role: {role}`, reads another customer's memory (Orrin Freight) and lists the team's keys. "
                "The isolation boundary is the team, not the key.",
                [ev("team get (ingest key)", f"caller_role {role}", role not in ("tenant_owner", "owner", "admin")),
                 ev(f"fact list --projects {ids['orrin']} (ingest key)",
                    f"{len(read_orrin)} fact(s) of Orrin Freight readable" if read_orrin else orrin_facts["message"],
                    not read_orrin),
                 ev("api-key list (ingest key)", f"{n_keys} keys listed" if keylist["rc"] == 0 else keylist["message"],
                    keylist["rc"] != 0)],
                "Mitigation Quillstack can state: one MemoryLake team per enterprise customer, with its own keys; "
                "inside a team, scoping is enforced by Quillstack's own service, which holds the key.", expected="no")
    out = dict(role=role, projects=[dict(id=p["id"], name=p["name"]) for p in projs], read_orrin=read_orrin,
               keys_listed=n_keys if keylist["rc"] == 0 else None, scoped=scoped)
    emit("scope", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: revoke

def revoke_step(cli: CLI, keys: dict, ids: dict, review: dict, pack: Pack) -> dict:
    k = keys["support-console"]
    s = key_cli(cli, k["name"])
    note("the old support console still works, and writes one last note before it is retired")
    f = (s.run("fact", "add", "--project", ids["halden"], review["support_note"], scoped=True) or {}).get("facts") or []
    note_id = f[0]["id"] if f else None
    selfr = s.attempt("api-key", "revoke", k["id"])
    note(f"the key tries to revoke itself → HTTP {selfr['status']} “{selfr['message']}”")
    cli.run("api-key", "revoke", k["id"])
    refused = until_refused(s, ["fact", "list", "--projects", ids["halden"]])
    note(f"its secret now: {'refused' if refused['refused'] else 'STILL ACCEPTED'} after {refused['after_s']}s"
         + (f" — HTTP {refused['status']} “{refused['message']}”" if refused["refused"] else ""))
    got = cli.attempt("api-key", "get", k["id"])
    note(f"api-key get {k['id']} → {'HTTP ' + str(got['status']) + ' ' + got['message'] if got['rc'] else 'still there'}")
    kept = any(x["id"] == note_id for x in list_facts(cli, ids["halden"]))
    note(f"the note it wrote is {'still in Halden’s memory' if kept else 'GONE'} ({note_id})")
    forget_key(k["name"])
    ok = selfr["status"] == 409 and refused["refused"] and got["rc"] != 0
    pack.answer("Q4", "yes" if ok else "no",
                f"Yes. `api-key revoke` cut the support console off on the next call ({refused['after_s']} s later) and "
                "the key no longer exists (`api-key get` 404). A key cannot revoke itself (409): revocation is done "
                "with another key, here the owner's. What the key wrote stays: it is the "
                "customer's memory, not the key's" + (" (its last note is still there)." if kept else "."),
                [ev(f"api-key revoke {k['id']} (with that key)", f"HTTP {selfr['status']} {selfr['message']}",
                    selfr["status"] == 409),
                 ev(f"api-key revoke {k['id']} (owner)", "revoked", True),
                 ev("fact list (revoked key)", f"HTTP {refused['status']} {refused['message']}" if refused["refused"]
                    else "accepted", refused["refused"]),
                 ev(f"api-key get {k['id']}", f"HTTP {got['status']} {got['message']}" if got["rc"] else "found",
                    got["rc"] != 0),
                 ev(f"fact list --projects {ids['halden']} (owner)", f"note {note_id} still present: {kept}", None)])
    out = dict(id=k["id"], self_revoke=dict(status=selfr["status"], message=selfr["message"]), refused=refused,
               get=dict(status=got["status"], message=got["message"]), note_kept=kept, note_id=note_id)
    emit("revoke", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: expiry

def expiry_step(cli: CLI, keys: dict, ids: dict, pack: Pack) -> dict:
    k = keys["contractor-export"]
    c = key_cli(cli, k["name"])
    exp = int(k.get("expires_at") or 0)
    if not exp:
        die("the contractor key has no expires_at")
    left = exp - now()
    if left > 0:
        before = c.attempt("proj", "list", scoped=True)
        note(f"{left}s before its expiry ({iso(exp)}): rc {before['rc']}")
        note("waiting for the expiry time — nobody revokes this key")
        while True:
            left = exp - now()
            if left <= 0:
                break
            emit("progress", f"  … {left:>3}s to go", left=left)
            time.sleep(min(10, left))
    time.sleep(2)
    refused = until_refused(c, ["proj", "list"])
    note(f"{now() - exp}s after expiry: {'refused' if refused['refused'] else 'STILL ACCEPTED'}"
         + (f" — HTTP {refused['status']} “{refused['message']}”" if refused["refused"] else ""))
    got = cli.run("api-key", "get", k["id"])
    status = got.get("status")
    note(f"api-key get {k['id']} → status “{status}”, expires_at {iso(got.get('expires_at'))} "
         "(the status field does not change at expiry; compare expires_at with the clock)")
    forget_key(k["name"])
    ok = refused["refused"] and k.get("worked_before")
    pack.answer("Q5", "yes" if ok else "no",
                "Yes. A key created with `--expires-at` worked until that moment and was refused right after it "
                f"(“{refused['message']}”), with no one revoking it.",
                [ev(f"api-key create --name {k['name']} --expires-at {exp}", f"expires {iso(exp)}", True),
                 ev("proj list (before expiry)", "rc 0" if k.get("worked_before") else "refused", k.get("worked_before")),
                 ev("proj list (after expiry)", f"HTTP {refused['status']} {refused['message']}" if refused["refused"]
                    else "accepted", refused["refused"]),
                 ev(f"api-key get {k['id']}", f"status {status}", None)],
                "`api-key list` / `get` keep `status: enabled` after expiry — an inventory report must compare "
                "`expires_at` with the clock to show the key as expired.")
    out = dict(id=k["id"], expires_at=exp, refused=refused, status_after=status)
    emit("expire", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: audit trail

def audit_step(cli: CLI, keys: dict, ids: dict, review: dict, pack: Pack) -> dict:
    ret = next(p for p in review["pinned"] if p["key"] == "retention")
    fid = ids["pinned"]["retention"]
    note("the bank signed amendment 1: drafts are kept 60 days, not 90. The owner updates the fact in place")
    cli.run("fact", "update", fid, "--project", ids["halden"], "--text", ret["amended"], scoped=True)
    tr = cli.run("fact", "trace", fid, "--project", ids["halden"], scoped=True) or {}
    rows = [dict(event=e.get("event"), source=e.get("source_kind"), ts=e.get("timestamp"),
                 text=e.get("new_fact"), old=e.get("old_fact"), source_event=e.get("source_event_id"),
                 fields=sorted(e.keys())) for e in tr.get("trace") or []]
    emit("text", "\n  fact trace " + fid + "\n  " + "\n  ".join(
        f"{r['event']:<7}{r['source']:<8}{str(r['ts'])[:19]}  {clip(r['text'], 70)}" for r in rows))
    # an extracted fact: its history points at the messages it was read from
    msgs = {m["id"]: m for m in _pages(cli, ["conv", "msg", "list", ids["conv"]], scoped=False)}
    extracted = [f for f in list_facts(cli, ids["halden"], echo=False)
                 if f["id"] not in ids["pinned"].values() and "Support note" not in f["fact"]]
    cook = None
    for f in extracted:
        t = cli.run("fact", "trace", f["id"], "--project", ids["halden"], scoped=True, echo=cook is None) or {}
        entry = next((e for e in t.get("trace") or [] if e.get("source_kind") == "COOK"), None)
        if entry:
            src = [msgs[s] for s in entry.get("source_entry_ids") or [] if s in msgs]
            src.sort(key=lambda m: m.get("sequence_no") or 0)
            cook = dict(fact_id=f["id"], text=f["fact"], event=entry.get("event"), ts=entry.get("timestamp"),
                        sources=[dict(seq=m.get("sequence_no"), at=m.get("timestamp"),
                                      text=clip(" ".join(b.get("text") or "" for b in m.get("content") or []), 90))
                                 for m in src])
            break
    if cook:
        emit("text", f"\n  extracted fact {cook['fact_id']}: {clip(cook['text'], 80)}\n  "
                     f"{cook['event']} COOK, read from {len(cook['sources'])} message(s):\n  " + "\n  ".join(
                         f"  #{s['seq']} {str(s['at'])[:16]}  {s['text']}" for s in cook["sources"]))
    fields = sorted({x for r in rows for x in r["fields"]})
    who_fields = [x for x in fields if any(w in x for w in ("key", "principal", "actor", "user", "by"))]
    meta = (cli.run("fact", "get", fid, "--project", ids["halden"], scoped=True, echo=False) or {}).get("metadata") or {}
    note(f"trace entry fields: {', '.join(fields)} — {'credential: ' + ', '.join(who_fields) if who_fields else 'none names the key that made the change'}")
    note(f"the fact's own metadata says written_by={meta.get('written_by')} — set by the writer, so self-reported")
    has_update = any(r["event"] == "UPDATE" for r in rows)
    pack.answer("Q6", "partial" if not who_fields else "yes",
                "Partly. `fact trace` records every change to a fact — ADD / UPDATE / FORGET, the old and new text, a "
                "server timestamp, MANUAL (an API call) or COOK (read from a conversation, with the ids of the "
                "messages it was read from). It does not record which API key made the change.",
                [ev(f"fact trace {fid}", " → ".join(f"{r['event']} {r['source']} {str(r['ts'])[:19]}" for r in reversed(rows)),
                    has_update)]
                + ([ev(f"fact trace {cook['fact_id']}", f"{cook['event']} COOK from messages "
                       + ", ".join(f"#{s['seq']}" for s in cook["sources"]), True)] if cook else [])
                + [ev("trace entry fields", ", ".join(fields), bool(who_fields))],
                "Quillstack stamps `metadata.written_by` with the integration's name on every pinned fact (`fact update "
                "--metadata`). That is the writer's own claim, not a server record — say so in the answer.",
                expected="partial")
    out = dict(fact_id=fid, trace=[{k: v for k, v in r.items() if k != "fields"} for r in rows], cook=cook,
               fields=fields, who_fields=who_fields, written_by=meta.get("written_by"))
    emit("audit", "", **out)
    return out


# --------------------------------------------------------------------------- step 9: offboarding

def offboard_step(cli: CLI, keys: dict, ids: dict, review: dict, pack: Pack) -> dict:
    note("Halden Mutual leaves: its conversation, its people and its project go, then the keys that served it")
    n_before = len(list_facts(cli, ids["halden"], echo=False))
    cli.run("conv", "delete", ids["conv"], scoped=True)
    for p in ("ines", "quill"):
        cli.run("actor", "delete", ids[p])
    cli.run("proj", "delete", ids["halden"], scoped=True)
    fl = cli.attempt("fact", "list", "--projects", ids["halden"], scoped=True)
    note(f"fact list on the deleted project → {'HTTP ' + str(fl['status']) + ' ' + fl['message'] if fl['rc'] else 'still answers'}")
    se = cli.attempt("search", "memo retention", "--projects", ids["halden"], scoped=True)
    note(f"search over it → {'HTTP ' + str(se['status']) + ' ' + se['message'] if se['rc'] else 'still answers'}")
    cv = cli.attempt("conv", "get", ids["conv"], scoped=True)
    note(f"conv get {ids['conv']} → {'HTTP ' + str(cv['status']) if cv['rc'] else 'still there'}")
    for k in demo_keys(cli, echo=False):
        if k.get("name") != cid("ingest-worker") and k.get("name") != cid("contractor-export"):
            continue
        cli.run("api-key", "revoke", str(k["id"]))
    remaining = demo_keys(cli)
    for name in [keys["ingest-worker"].get("rotated_to")]:
        if name:
            forget_key(name)
    note(f"demo keys left on the team: {len(remaining)}")
    ok = fl["rc"] != 0 and se["rc"] != 0 and cv["rc"] != 0 and not remaining
    pack.answer("Q7", "yes" if ok else "no",
                f"Yes. Deleting the customer's conversation, people and project removed its memory ({n_before} facts): "
                "`fact list` on the project answers 404 and a search that names it is rejected (400). The keys that "
                "served the customer were revoked and are gone from the key list.",
                [ev(f"proj delete {ids['halden']}", "deleted", True),
                 ev(f"fact list --projects {ids['halden']}", f"HTTP {fl['status']} {fl['message']}" if fl["rc"] else "answered",
                    fl["rc"] != 0),
                 ev(f"search --projects {ids['halden']}", f"HTTP {se['status']} {se['message']}" if se["rc"] else "answered",
                    se["rc"] != 0),
                 ev(f"conv get {ids['conv']}", f"HTTP {cv['status']}" if cv["rc"] else "found", cv["rc"] != 0),
                 ev("api-key list", f"{len(remaining)} demo key(s) left", not remaining)],
                "Delete the conversation explicitly: `proj delete` does not delete conversations.")
    out = dict(facts_before=n_before, fact_list=dict(status=fl["status"], message=fl["message"]),
               search=dict(status=se["status"], message=se["message"]), conv=dict(status=cv["status"]),
               keys_left=len(remaining))
    emit("offboard", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    review = load_review()
    conv = find_conv(cli, review["conversation"]["slug"], echo=not quiet)
    if conv:
        cli.run("conv", "delete", conv["id"], scoped=True)
        note("deleted the Halden conversation (`proj delete` does not delete conversations)")
    n = 0
    for slug in review["tenants"]:
        p = find_project(cli, slug, echo=not quiet and slug == "halden")
        if p:
            cli.run("proj", "delete", p["id"], scoped=True)
            n += 1
    note(f"deleted {n} project(s) (their facts go with them)")
    for person in review["people"].values():
        a = find_actor(cli, person["slug"], echo=not quiet and person["slug"] == "ines")
        if a:
            cli.run("actor", "delete", a["id"])
            note(f"deleted {person['name']}")
    keys = demo_keys(cli, echo=not quiet)
    for k in keys:
        cli.run("api-key", "revoke", str(k["id"]))
    note(f"revoked {len(keys)} demo key(s) (only keys named {PREFIX}-…; never the key this demo runs with)")
    if KEYS_DIR.exists():
        shutil.rmtree(KEYS_DIR)
        note("deleted the integration-key profiles under .memorylake-demo/keys/")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    review = load_review()
    t0 = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    pack = Pack(review)
    emit("review", "", vendor=review["vendor"], buyer=review["buyer"], questions=review["questions"],
         team=dict(name=cli.team.get("name"), type=cli.team.get("type"), role=cli.team.get("caller_role")))  # type: ignore[attr-defined]
    banner(2, TOTAL, "Q1 Credentials — one key per integration, the secret shown once")
    if reset or leftovers(cli):
        note("found objects from an earlier run" if not reset else "--reset")
        note("keys are one-shot, so every run starts from nothing: cleaning up first")
        cleanup(cli, quiet=True)
    keys = issue_keys(cli, review, pack, run_id)
    banner(3, TOTAL, "Halden Mutual's memory, written through the ingest worker's key")
    ids = tenant_memory(cli, review, keys)
    banner(4, TOTAL, "Q2 Rotation — new secret, same key, same memory")
    rot = rotate_step(cli, keys, ids, pack, run_id)
    banner(5, TOTAL, "Q3 Least privilege — what one integration key can reach")
    sc = scope_step(cli, keys, ids, review, pack)
    banner(6, TOTAL, "Q4 Revocation — retire the support console's key")
    rv = revoke_step(cli, keys, ids, review, pack)
    banner(7, TOTAL, "Q5 Temporary access — the contractor's key expires on its own")
    ex = expiry_step(cli, keys, ids, pack)
    banner(8, TOTAL, "Q6 Audit trail — what changed, when, from where, by which key")
    au = audit_step(cli, keys, ids, review, pack)
    banner(9, TOTAL, "Q7 Offboarding — delete Halden's memory and show it is gone")
    ob = offboard_step(cli, keys, ids, review, pack)
    meta = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), team=cli.team.get("name"),  # type: ignore[attr-defined]
                workspace=cli.workspace, base_url=cli.base_url)
    doc = write_pack(pack, meta)
    show_pack(doc)
    summary = dict(when=meta["when"], seconds=int(time.time() - t0), counts=doc["counts"],
                   verdicts={a["id"]: a["verdict"] for a in doc["answers"]},
                   rotate_refused_s=rot["refused"]["after_s"] if rot["refused"]["refused"] else None,
                   revoke_refused_s=rv["refused"]["after_s"] if rv["refused"]["refused"] else None,
                   expiry_refused=ex["refused"]["refused"], self_revoke=rv["self_revoke"]["status"],
                   role=sc["role"], facts_same_after_rotate=rot["same"], trace_names_key=bool(au["who_fields"]),
                   offboard_404=ob["fact_list"]["status"], keys_left=ob["keys_left"])
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    emit("summary", "", **summary)
    return summary


def show_pack(doc: dict | None = None) -> dict:
    if doc is None:
        p = OUT / "security-review-halden.json"
        if not p.exists():
            die("no evidence pack yet; run `python3 demo.py` first")
        doc = json.loads(p.read_text(encoding="utf-8"))
    c = doc["counts"]
    sym = {"yes": "✓ yes   ", "no": "✗ no    ", "partial": "◐ partly"}
    emit("text", f"\n  {doc['vendor']} — security review answers for {doc['buyer']}  ({doc['generated_at']})\n"
                 f"  {c['yes']} yes · {c['partial']} partly · {c['no']} no\n\n  " + "\n  ".join(
                     f"{a['id']}  {sym[a['verdict']]}  {a['topic']:<17} {clip(a['answer'], 84)}" for a in doc["answers"])
         + f"\n\n  full pack: out/security-review-halden.md (evidence for every answer) · .json")
    emit("pack", "", **doc)
    return doc


def show_keys(cli: CLI) -> None:
    keys = demo_keys(cli)
    if not keys:
        emit("text", "\n  no demo keys on this team (they are all revoked at the end of a run)")
        return
    emit("text", "\n  " + "\n  ".join(f"{k['id']:<7}{k['name']:<28}{k.get('key_prefix', ''):<10}"
                                      f"{k.get('status', ''):<9}expires {iso(k.get('expires_at'))}"
                                      + ("  (past)" if k.get("expires_at") and k["expires_at"] < now() else "")
                                      for k in keys))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "pack", "keys", "cleanup"],
                    help="run = full demo (default); pack = print the last evidence pack; keys = the demo's keys "
                         "on your team (prefixes only); cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="clean up anything left by an earlier run first (a run "
                                                         "does this on its own when it finds leftovers)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (keys masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "pack":
        show_pack()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "keys":
        banner(2, 2, "The demo's keys on your team")
        show_keys(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py pack` prints the answers again; the full evidence is in "
          "out/security-review-halden.md. `python3 demo.py cleanup` removes the second customer's project.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {mask(str(e))}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        # The failing command can be `auth login --api-key …`: never print a key.
        shown = mask(shlex.join(e.cmd)) if e.cmd else "(memorylake)"
        print(f"\ncommand failed: {shown}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

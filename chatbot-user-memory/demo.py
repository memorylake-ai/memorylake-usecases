#!/usr/bin/env python3
"""
Add long-term, per-user memory to a chatbot — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
"Nimbus" is a travel concierge chatbot.  Two users talk to it over several
sessions and channels (web widget, mobile app, WhatsApp, Slack).  Every session
is written to MemoryLake as a conversation between the user and the bot; the
facts MemoryLake extracts land on the *user*, not on the bot or the project, so
each user has a memory of their own.  When a user comes back on a new channel,
the bot retrieves that memory in one call and knows them — and one user's memory
never leaks into another's.  A user can also ask to be forgotten, fact by fact.

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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cbm"  # memorylake-usecases / chatbot-memory

BOT = dict(key="nimbus", custom_id=f"{PREFIX}-nimbus", display="Nimbus", role="travel concierge bot")
PROJECT = dict(
    custom_id=f"{PREFIX}-nimbus-sessions",
    name="Nimbus — chat sessions",
    description="Every chat session of the Nimbus concierge bot. Demo data.",
)
LLM_MODEL = "claude-haiku-4-5-20251001"


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

def load_users() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "users").glob("*.json"))]


def user_custom_id(user: dict) -> str:
    # In your product this is simply your own user id; the prefix keeps demo data apart from yours.
    return f"{PREFIX}-user-{user['user_id']}"


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, custom_id: str, display: str, actor_type: str, description: str, tags: str) -> str:
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", display,
                        "--type", actor_type, "--tags", tags, "--description", description)
        note(f"created {actor_type.lower()} actor {display} → {actor['id']}")
    else:
        note(f"actor {display} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    return actor["id"]


def setup_actors(cli: CLI) -> tuple[str, dict[str, str]]:
    bot_id = ensure_actor(cli, BOT["custom_id"], BOT["display"], "ASSISTANT", BOT["role"], "bot,demo")
    emit("bot", "", key=BOT["key"], id=bot_id, display=BOT["display"])
    users: dict[str, str] = {}
    for u in load_users():
        users[u["key"]] = ensure_actor(cli, user_custom_id(u), u["display"], "HUMAN",
                                       f"end user {u['user_id']} of the Nimbus bot", "end-user,demo")
        emit("user", "", key=u["key"], id=users[u["key"]], display=u["display"], user_id=u["user_id"])
    return bot_id, users


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def delete_conversations(cli: CLI) -> int:
    removed = 0
    for u in load_users():
        for s in u["sessions"]:
            conv = cli.try_run("conv", "get", f"{PREFIX}-{s['custom_id']}", "--by-custom-id", scoped=True)
            if conv is not None:
                cli.run("conv", "delete", conv["id"], scoped=True)
                removed += 1
    return removed


def ensure_project(cli: CLI, reset: bool) -> str:
    proj = find_project(cli)
    if proj is not None and reset:
        note(f"--reset: deleting the demo sessions and project {proj['id']}")
        delete_conversations(cli)
        cli.run("proj", "delete", proj["id"], scoped=True)
        proj = None
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=True)
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=False)
    return proj["id"]


# --------------------------------------------------------------------------- step 3: replay the sessions

def replay_sessions(cli: CLI, project_id: str, bot_id: str, users: dict[str, str]) -> list[str]:
    conv_ids: list[str] = []
    for u in load_users():
        uid = users[u["key"]]
        for s in u["sessions"]:
            cid_custom = f"{PREFIX}-{s['custom_id']}"
            conv = cli.try_run("conv", "get", cid_custom, "--by-custom-id", scoped=True)
            done = 0
            if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
                note(f"session “{s['title']}” belongs to a deleted project; recreating it")
                cli.run("conv", "delete", conv["id"], scoped=True)
                conv = None
            if conv is None:
                # One conversation per chat session, between this user and the bot. The channel goes in
                # metadata; the user's identity is the actor, so every session adds to the same memory.
                conv = cli.run("conv", "create", "--custom-id", cid_custom, "--project", project_id,
                               "--actors", f"{uid},{bot_id}", "--kind", "DIRECT", "--name", f"{u['display']} — {s['title']}",
                               "--metadata", f"channel={s['channel']}", "--metadata", f"user_id={u['user_id']}", scoped=True)
            else:
                done = len((cli.run("conv", "msg", "list", conv["id"], "--page-size", "50") or {}).get("items") or [])
                note(f"session “{s['title']}” exists with {done} message(s) → {conv['id']}")
            conv_ids.append(conv["id"])
            emit("session", "", user=u["key"], custom_id=s["custom_id"], id=conv["id"], title=s["title"],
                 channel=s["channel"], date=s["date"], turns=len(s["turns"]), done=done)
            start = datetime.fromisoformat(s["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            parent = conv.get("current_message_id")
            for i, (who, text) in enumerate(s["turns"], start=1):
                if i <= done:
                    continue
                ts = (start + timedelta(seconds=40 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
                args = ["conv", "msg", "append", conv["id"], "--actor", uid if who == "user" else bot_id,
                        "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", text]
                if parent:
                    args += ["--parent", parent]
                msg = cli.run(*args, scoped=True)
                parent = msg["id"]
                emit("turn", "", user=u["key"], custom_id=s["custom_id"], index=i, who=who)
            if done < len(s["turns"]):
                note(f"{len(s['turns']) - done} message(s) appended to “{u['display']} — {s['title']}” ({s['channel']})")
    return conv_ids


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for cid in sorted(pending):
            try:
                status = cli.run("conv", "cook-status", cid, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                note("cook-status probe failed on the network; will try again on the next poll")
                continue
            if status.get("cook_finished"):
                pending.discard(cid)
                emit("cooked", "", id=cid)
        first = False
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} session(s) still being turned into memory ({int(time.time() - t0)}s)",
                     pending=len(pending), elapsed=int(time.time() - t0))
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} session(s) still processing server-side")
    else:
        note(f"memory ready for all sessions in {int(time.time() - t0)}s")


# --------------------------------------------------------------------------- step 4: what the bot knows

def fact_date(f: dict) -> str:
    m = re.search(r"\(as of (\d{4}-\d{2}-\d{2})\)", f.get("fact", ""))
    return m.group(1) if m else (f.get("created_at") or "")[:10]


def list_user_facts(cli: CLI, actor_id: str) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    return sorted(facts, key=fact_date)


def show_profiles(cli: CLI, users: dict[str, str]) -> dict[str, list[dict]]:
    profiles = {}
    for u in load_users():
        facts = list_user_facts(cli, users[u["key"]])
        profiles[u["key"]] = facts
        lines = [f"\n  {u['display']} ({u['user_id']}) — {len(facts)} facts, all owned by the user's actor:"]
        lines += [f"   - {f['fact']}" for f in facts]
        emit("text", "\n".join(lines))
        emit("profile", "", user=u["key"], facts=[dict(id=f["id"], fact=f["fact"], date=fact_date(f)) for f in facts])
    return profiles


# --------------------------------------------------------------------------- step 5: a returning user

def search_user(cli: CLI, actor_id: str, query: str, top_k: int = 8) -> list[dict]:
    res = cli.run("search", query, "--actors", actor_id, "--types", "fact", "--top-k", str(top_k), scoped=True) or {}
    return [dict(id=f.get("id"), fact=f.get("fact"), score=f.get("score")) for f in res.get("facts") or []]


def context_block(display: str, facts: list[dict]) -> str:
    """What your chatbot backend prepends to the model prompt for this turn."""
    lines = [f"<memory user=\"{display}\">"]
    lines += [f"- {f['fact']}" for f in facts] or ["- (nothing known yet)"]
    lines.append("</memory>")
    return "\n".join(lines)


def returning_users(cli: CLI, users: dict[str, str]) -> dict[str, dict]:
    out = {}
    for u in load_users():
        r = u["returning"]
        facts = search_user(cli, users[u["key"]], r["question"])
        block = context_block(u["display"], facts)
        emit("text", f"\n  {u['display']} comes back on {r['channel']}: “{r['question']}”\n"
                     f"  One search, scoped to this user, becomes the memory block for the model:\n\n"
                     + "\n".join("    " + l for l in block.splitlines()))
        out[u["key"]] = dict(question=r["question"], channel=r["channel"], facts=facts, block=block)
        emit("context", "", user=u["key"], question=r["question"], channel=r["channel"], facts=facts, block=block)
    return out


def isolation_check(cli: CLI, users: dict[str, str]) -> None:
    """A query that only one user's memory can answer must return nothing for the other."""
    probes = [("alice", "bob", "steak and company invoices"), ("bob", "alice", "peanut allergy and vegetarian food")]
    results = []
    for asker, owner, query in probes:
        if asker not in users or owner not in users:
            continue
        leaked = search_user(cli, users[asker], query, top_k=5)
        theirs = search_user(cli, users[owner], query, top_k=5)
        # The ranker returns the closest facts it has; a leak means one of the owner's facts shows up.
        owner_texts = {f["fact"] for f in theirs}
        leak = [f for f in leaked if f["fact"] in owner_texts]
        verdict = "LEAK" if leak else "isolated"
        results.append(dict(asker=asker, owner=owner, query=query, leaked=len(leak), owner_hits=len(theirs), verdict=verdict))
        note(f"“{query}” in {asker}'s memory: {len(leak)} of {owner}'s facts leaked ({verdict}); in {owner}'s own memory: {len(theirs)} hits")
    emit("isolation", "", results=results)


# --------------------------------------------------------------------------- step 6: the right to be forgotten

def forget(cli: CLI, users: dict[str, str]) -> None:
    for u in load_users():
        f = u.get("forget")
        if not f:
            continue
        actor_id = users[u["key"]]
        emit("text", f"\n  {u['display']}: “{f['request']}”")
        before = search_user(cli, actor_id, f["query"], top_k=3)
        if not before:
            note("nothing matched; nothing to delete")
            emit("forgot", "", user=u["key"], request=f["request"], deleted=None, before=[], after=[])
            continue
        target = before[0]
        # Facts are immutable, so forgetting is a delete. One command, the user's actor, the fact id.
        cli.run("fact", "delete", "--actor", actor_id, target["id"], scoped=True)
        note(f"deleted: {target['fact']}")
        after = search_user(cli, actor_id, f["query"], top_k=3)
        still = [x for x in after if x["id"] == target["id"]]
        note("the same search no longer returns it" if not still else "WARNING: the fact is still returned")
        emit("forgot", "", user=u["key"], request=f["request"], deleted=target, before=before, after=after)


# --------------------------------------------------------------------------- optional: a real reply through the Model Router

def llm_reply(cli: CLI, contexts: dict[str, dict]) -> None:
    key = cli.secret or os.environ.get("MEMORYLAKE_API_KEY", "")
    if not key:
        note("no API key in hand (you are using a stored login), so the Model Router call is skipped")
        return
    router = cli.base_url.split("/openapi/")[0] + "/v1/chat/completions"
    for u in load_users():
        ctx = contexts.get(u["key"])
        if not ctx:
            continue
        body = {"model": LLM_MODEL, "max_tokens": 200, "messages": [
            {"role": "system", "content": "You are Nimbus, a travel concierge bot. Use the memory block; keep it to three short sentences.\n\n" + ctx["block"]},
            {"role": "user", "content": ctx["question"]}]}
        emit("cmd", f"curl {router} -H 'Authorization: Bearer sk-…' -d '{{\"model\": \"{LLM_MODEL}\", …}}'")
        req = urllib.request.Request(router, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                reply = json.load(resp)["choices"][0]["message"]["content"].strip()
            emit("text", f"\n  Nimbus → {u['display']}: {reply}")
            emit("llm", "", user=u["key"], reply=reply)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if e.code in (402, 429) or "quota" in msg.lower():
                note("the Model Router needs model quota on your account (insufficient_quota); the memory steps above need none. Skipping.")
                emit("llm", "", user=u["key"], reply=None, skipped="quota")
                return
            raise


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} session conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted project")
    for u in load_users():
        actor = cli.try_run("actor", "get", user_custom_id(u), "--by-custom-id")
        if actor:
            # Deleting the actor deletes the user's memory with it: the whole-account "forget me".
            cli.run("actor", "delete", actor["id"])
            note(f"deleted user {u['display']} and every fact owned by them")
    bot = cli.try_run("actor", "get", BOT["custom_id"], "--by-custom-id")
    if bot:
        cli.run("actor", "delete", bot["id"])
        note("deleted the bot actor")


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False, with_llm: bool = False) -> None:
    total = 6 + (1 if with_llm else 0)
    banner(2, total, "Set up — the bot, one actor per end user, one project for the sessions")
    bot_id, users = setup_actors(cli)
    project_id = ensure_project(cli, reset)

    banner(3, total, "Replay the sessions — every chat becomes a conversation between user and bot")
    conv_ids = replay_sessions(cli, project_id, bot_id, users)
    wait_for_memory(cli, conv_ids)

    banner(4, total, "What the bot knows — the facts landed on each user, not on the bot")
    show_profiles(cli, users)

    banner(5, total, "A returning user on a new channel — one scoped search is the memory for this turn")
    contexts = returning_users(cli, users)
    isolation_check(cli, users)

    banner(6, total, "The right to be forgotten — delete one fact at the user's request")
    forget(cli, users)

    if with_llm:
        banner(7, total, "Optional — a real reply through the Model Router, same API key")
        llm_reply(cli, contexts)

    OUT.mkdir(exist_ok=True)
    (OUT / "context-blocks.md").write_text(
        "\n\n".join(f"## {k} — {v['channel']}\n\n> {v['question']}\n\n```\n{v['block']}\n```" for k, v in contexts.items()),
        encoding="utf-8")
    note(f"memory blocks written to {OUT / 'context-blocks.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "profiles", "cleanup"],
                    help="run = full demo (default); profiles = only print what the bot knows; cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo project and sessions first")
    ap.add_argument("--with-llm", action="store_true", help="also generate a real reply via the Model Router (needs model quota)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "profiles": 2}.get(args.command, 6 + (1 if args.with_llm else 0))
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "profiles":
        banner(2, 2, "What the bot knows about each user")
        users = {}
        for u in load_users():
            a = cli.try_run("actor", "get", user_custom_id(u), "--by-custom-id")
            if a is None:
                die("the demo users do not exist yet; run `python3 demo.py` first")
            users[u["key"]] = a["id"]
        show_profiles(cli, users)
        return
    run_pipeline(cli, reset=args.reset, with_llm=args.with_llm)
    print("\nDone. `python3 demo.py profiles` shows the memory again; `python3 demo.py cleanup` removes the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        print(f"\ncommand failed: {shlex.join(e.cmd) if e.cmd else '(memorylake)'}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

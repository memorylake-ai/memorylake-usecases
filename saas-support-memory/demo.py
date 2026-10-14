#!/usr/bin/env python3
"""
Small SaaS support memory — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallyfold (fictional) sells invoicing software; four people answer its
support queue.  Two kinds of memory sit behind every ticket: the team's known
issues (one project, every issue pinned with the conditions it applies under,
its workaround, and what that workaround breaks), and one memory per customer
(their own actor: browser, currencies, integrations, how they want replies —
learned from past tickets).

Today, 2026-10-14, two customers write in with the same symptom: the totals
on their PDF invoices are blank.  Looked up by symptom alone, the known-issues
memory gives both the same top hit and the same reply.  For one of them it is
wrong — and the workaround it suggests is itself a known bug on their kind of
account.  Read together with each customer's memory, the answers split: every
condition is checked against what the customer told us, citing the ticket and
date it came from, and the workaround another issue forbids is held back.
What was said and what was withheld goes back into the customer's memory, so
the next agent picks up where this one left off.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ssm"  # memorylake-usecases / saas-support-memory

PROJECTS = dict(
    ki=dict(key="known-issues", name="Tallyfold support — known issues",
            description="Team-wide known issues: conditions, workaround, what the workaround breaks. Demo data."),
    tickets=dict(key="tickets", name="Tallyfold support — ticket conversations",
                 description="Past ticket conversations (kept out of the known-issues project). Demo data."),
)




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


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def customer(story: dict, key: str) -> dict:
    c = next((c for c in story["customers"] if c["key"] == key or c["short"].lower() == key.lower()), None)
    if c is None:
        die(f"unknown customer {key!r}; data/story.json has: {', '.join(c['key'] for c in story['customers'])}")
    return c


def ki_meta(k: dict, story: dict) -> dict:
    return dict(kind="known_issue", ki=k["ki"], symptom=k["symptom"], status=k["status"], applies_when=k["applies_when"],
                workaround=k["workaround"], workaround_id=k.get("workaround_id"), breaks=k.get("breaks"),
                reported=k["reported"], verified_by=story["agents"][k["verified_by"]]["name"])


def env_meta(c: dict, e: dict, story: dict) -> dict:
    return dict(kind="environment", attr=e["attr"], value=e["value"], ticket=c["ticket"]["id"],
                date=c["ticket"]["date"], recorded_by=story["agents"]["dana"]["name"])


# --------------------------------------------------------------------------- reading scopes

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


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", flag, scope_id], echo=echo)


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


def known_issues(cli: CLI, ids: dict, echo: bool = True) -> dict[str, dict]:
    """fact id → known issue, read from the pinned facts' metadata (the whole project, every page)."""
    out = {}
    for f in list_facts(cli, "--projects", ids["ki"], echo=echo):
        m = f.get("metadata") or {}
        if m.get("kind") == "known_issue":
            out[f["id"]] = dict(m, id=f["id"], text=f.get("fact"))
    return out


def environment(cli: CLI, actor_id: str, echo: bool = True) -> tuple[dict, list[dict]]:
    """A customer's memory: pinned environment facts by attribute, plus everything else on the actor."""
    env, other = {}, []
    for f in list_facts(cli, "--actors", actor_id, echo=echo):
        m = f.get("metadata") or {}
        if m.get("kind") == "environment":
            env[m["attr"]] = dict(m, id=f["id"], text=f.get("fact"))
        else:
            other.append(dict(id=f["id"], text=f.get("fact"), kind=m.get("kind") or "extracted", meta=m))
    return env, other


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, key: str, name: str, description: str, tags: str) -> str:
    actor = find(cli, "actor", key)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", name,
                        "--description", description, "--tags", tags)
        note(f"created {name} → {actor['id']}")
    else:
        note(f"{name} already exists → {actor['id']}")
    bind_actor(cli, actor["id"])
    return actor["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    for k, p in PROJECTS.items():
        proj = find(cli, "proj", p["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                           "--description", p["description"], scoped=True)
            note(f"created “{p['name']}” → {proj['id']}")
        else:
            note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[k] = proj["id"]
    for c in story["customers"]:
        ids[c["key"]] = ensure_actor(cli, c["key"], c["name"], c["description"], "customer")
    for k, a in story["agents"].items():
        ids[k] = ensure_actor(cli, a["key"], a["name"], a["description"], "support-agent")
    lines = [f"\n  Known issues         {ids['ki']}  (team-wide: one project every agent reads)",
             f"  Ticket history       {ids['tickets']}  (past conversations live here, not in known issues)"]
    lines += [f"  {c['name']:<20} {ids[c['key']]}  (customer memory: one actor per customer)" for c in story["customers"]]
    lines += [f"  {a['name']:<20} {ids[k]}  (support agent)" for k, a in story["agents"].items()]
    emit("text", "\n".join(lines))
    emit("setup", "", ids=ids, projects={k: p["name"] for k, p in PROJECTS.items()},
         customers=[dict(key=c["key"], name=c["name"], description=c["description"]) for c in story["customers"]],
         agents=[dict(key=k, name=a["name"], description=a["description"]) for k, a in story["agents"].items()])
    return ids


# --------------------------------------------------------------------------- pinning

def pin(cli: CLI, flag: str, scope_id: str, items: list[tuple[str, dict]], what: str) -> dict[str, dict]:
    """Pin the texts the scope does not hold yet — one `fact add` call for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). Returns text → {id, new}."""
    plural = {"--project": "--projects", "--actor": "--actors"}[flag]
    live = {f.get("fact"): f for f in list_facts(cli, plural, scope_id, echo=False)}
    out, missing = {}, []
    for text, meta in items:
        f = live.get(text)
        if f is None:
            missing.append((text, meta))
            continue
        out[text] = dict(id=f["id"], new=False)
        if (f.get("metadata") or {}) != meta:
            cli.run("fact", "update", f["id"], flag, scope_id, "--metadata", json.dumps(meta), scoped=True, echo=False)
    if missing:
        res = cli.run("fact", "add", flag, scope_id, *[t for t, _ in missing], scoped=True) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, flag, scope_id, "--metadata", json.dumps(meta), scoped=True, echo=i == 0)
            out[text] = dict(id=fid, new=True)
        if len(missing) > 1:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way ({what})")
    return out


# --------------------------------------------------------------------------- step 3: known issues

def cond_text(applies_when: dict) -> str:
    words = {"multi": "more than one", "unset": "not set"}
    return " and ".join(f"{a.replace('_', ' ')} {words.get(v, v)}" for a, v in applies_when.items())


def show_kis(kis: list[dict]) -> None:
    lines = [f"\n  {'issue':<7} {'symptom':<19} {'applies when':<44} workaround / note"]
    for k in sorted(kis, key=lambda k: k["ki"]):
        extra = f"BREAKS the “{k['breaks']}” workaround" if k.get("breaks") else clip(k["workaround"], 58)
        lines.append(f"  {k['ki']:<7} {k['symptom']:<19} {clip(cond_text(k['applies_when']), 44):<44} {extra}")
        lines.append(f"  {'':<7} {k['status']}, reported {k['reported']}, verified by {k['verified_by']} · {k['id']}")
    emit("text", "\n".join(lines))


def pin_known_issues(cli: CLI, ids: dict, story: dict) -> dict:
    note(f"{len(story['known_issues'])} known issues go onto the known-issues project in one `fact add`; each then gets "
         "metadata: symptom, the conditions it applies under, its workaround, what it breaks, status")
    got = pin(cli, "--project", ids["ki"], [(k["text"], ki_meta(k, story)) for k in story["known_issues"]],
              "symptom, conditions, workaround, breaks, status")
    if not any(g["new"] for g in got.values()):
        note("the known issues were pinned by an earlier run; reading them back")
    kis = list(known_issues(cli, ids).values())
    show_kis(kis)
    emit("known_issues", "", items=kis)
    return {k["ki"]: k for k in kis}


# --------------------------------------------------------------------------- step 4: customer memory

def list_messages(cli: CLI, conv_id: str) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, echo=False) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return msgs


def wait_for_cook(cli: CLI, conv_ids: list[str], timeout: int = 600) -> int:
    t0 = last = time.time()
    left, first = list(conv_ids), True
    while left and time.time() - t0 < timeout:
        for cid_ in list(left):
            try:
                status = cli.run("conv", "cook-status", cid_, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                status = {}
            first = False
            if status.get("cook_finished"):
                left.remove(cid_)
        if not left:
            break
        if time.time() - last >= 30:
            emit("progress", f"  … still turning {len(left)} ticket conversation(s) into memory ({int(time.time() - t0)}s)")
            last = time.time()
        time.sleep(5)
    if left:
        note(f"gave up after {timeout}s; {len(left)} conversation(s) still processing server-side")
        return -1
    return int(time.time() - t0)


def file_ticket(cli: CLI, ids: dict, story: dict, c: dict) -> tuple[str, int]:
    t = c["ticket"]
    conv = find(cli, "conv", f"ticket-{c['key']}", echo=False)
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", cid(f"ticket-{c['key']}"), "--project", ids["tickets"],
                       "--actors", f"{ids[c['key']]},{ids['dana']}", "--kind", "DIRECT", "--name", t["title"],
                       "--metadata", f"ticket={t['id']}", "--metadata", f"customer={c['name']}", scoped=True)
    done = len(list_messages(cli, conv["id"]))
    speaker = {"customer": ids[c["key"]], "agent": ids["dana"]}
    start = datetime.fromisoformat(t["start"].replace("Z", "+00:00"))
    n = 0
    for i, (k, words) in enumerate(t["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cli.run("conv", "msg", "append", conv["id"], "--actor", speaker[k], "--custom-id", f"msg-{i:02d}",
                "--timestamp", ts, "--text", words, scoped=True, echo=n == 0)
        n += 1
        emit("turn", "", customer=c["key"], index=i, who=k, line=words)
    if n > 1:
        note(f"… and {n - 1} more message(s) appended the same way")
    return conv["id"], n


def show_profile(c: dict, env: dict, other: list[dict]) -> None:
    lines = [f"\n  {c['name']} — what support knows (`fact list --actors …`):"]
    for attr in ("browser", "currencies", "default_currency", "integrations", "contact"):
        e = env.get(attr)
        if e:
            lines.append(f"    {attr:<17} {e['value']:<22} from {e['ticket']}, {e['date']} · {e['id']}")
    extracted = [o for o in other if o["kind"] == "extracted"]
    tickets = [o for o in other if o["kind"] == "ticket"]
    for t in tickets:
        lines.append(f"    ticket record     {clip(t['text'], 100)}")
    if extracted:
        lines.append(f"    + {len(extracted)} fact(s) MemoryLake extracted from the ticket conversation (shown, not relied on):")
        lines += [f"      · {clip(o['text'], 100)}" for o in extracted[:6]]
        if len(extracted) > 6:
            lines.append(f"      … and {len(extracted) - 6} more")
    emit("text", "\n".join(lines))


def customer_memory(cli: CLI, ids: dict, story: dict) -> dict:
    note("each customer's September ticket is a DIRECT conversation (customer ↔ Dana) in the ticket-history project; "
         "MemoryLake turns it into memory on the customer's actor")
    convs, appended = [], 0
    for c in story["customers"]:
        conv_id, n = file_ticket(cli, ids, story, c)
        convs.append(conv_id)
        appended += n
    if appended:
        secs = wait_for_cook(cli, convs)
        if secs >= 0:
            note(f"both ticket conversations were turned into memory in {secs}s")
    else:
        note("the ticket conversations were filed by an earlier run")
    note("what a support agent must be able to rely on is pinned on the customer's actor, with the ticket and date it came from")
    profiles = []
    for c in story["customers"]:
        pin(cli, "--actor", ids[c["key"]], [(e["text"], env_meta(c, e, story)) for e in c["environment"]],
            "attribute, value, source ticket, date")
    for c in story["customers"]:
        env, other = environment(cli, ids[c["key"]])
        show_profile(c, env, other)
        profiles.append(dict(key=c["key"], name=c["name"], ticket=c["ticket"]["id"], env=env,
                             extracted=[o for o in other if o["kind"] == "extracted"]))
    tickets_proj = sum(1 for _ in list_facts(cli, "--projects", ids["tickets"], echo=False))
    note(f"{tickets_proj} more fact(s) were extracted onto the ticket-history project; the known-issues project holds only the pinned issues")
    out = dict(profiles=profiles, conversations=convs, appended=appended, tickets_project=tickets_proj)
    emit("customers", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: by symptom only

def symptom_search(cli: CLI, ids: dict, kis: dict, text: str, top_k: int = 3, echo: bool = True) -> list[dict]:
    res = cli.run("search", text, "--projects", ids["ki"], "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    by_id = {k["id"]: k for k in kis.values()}
    return [dict(id=f.get("id"), text=f.get("fact") or f.get("content"), ki=(by_id.get(f.get("id")) or {}).get("ki"))
            for f in res.get("facts") or []]


def symptom_only(cli: CLI, ids: dict, story: dict, kis: dict) -> dict:
    note(f"{story['today']}: two new tickets, handled by {story['agents']['leo']['name']} (started this week). "
         "First, the way a support tool that sees one ticket at a time would do it: the ticket text against the known issues, nothing else")
    rows = []
    for c in story["customers"]:
        t = c["today"]
        emit("text", f"\n  {t['id']} {c['name']}: “{t['text']}”")
        hits = symptom_search(cli, ids, kis, t["text"])
        lines = [f"    {i}. {h['ki'] or '—'}  {clip(h['text'], 96)}" for i, h in enumerate(hits, 1)]
        top = next((h for h in hits if h["ki"]), None)
        reply = kis[top["ki"]]["workaround"] if top else None
        lines.append(f"    → reply: {top['ki'] + ' — ' + reply if top else 'no known issue found'}")
        emit("text", "\n".join(lines))
        rows.append(dict(customer=c["key"], name=c["name"], ticket=t["id"], text=t["text"], hits=hits,
                         top=top and top["ki"], reply=reply))
    tops = {r["top"] for r in rows}
    same = len(tops) == 1 and None not in tops
    emit("text", f"\n  {'Both tickets get the same reply: ' + rows[0]['top'] if same else 'The two tickets get different top hits'}"
                 " — the lookup knows the symptom, not the customer.")
    out = dict(rows=rows, same=same)
    emit("symptom_only", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: with customer memory

WORDS = {"multi": "more than one currency", "unset": "no default currency"}


def cond_ok(want: str, value: str | None):
    if value is None:
        return None
    if want == "multi":
        return "+" in value
    if want == "unset":
        return value.strip().lower() == "unset"
    return want.lower() in value.lower()


def conditions(k: dict, env: dict) -> list[dict]:
    out = []
    for attr, want in k["applies_when"].items():
        e = env.get(attr)
        out.append(dict(attr=attr, want=want, value=e and e["value"], ok=cond_ok(want, e and e["value"]),
                        source=e and f"{e['ticket']}, {e['date']}", fact=e and e["id"]))
    return out


def evaluate(k: dict, env: dict, kis: dict) -> dict:
    conds = conditions(k, env)
    applies = all(c["ok"] for c in conds)
    unknown = any(c["ok"] is None for c in conds)
    blockers = []
    for other in kis.values():
        if k.get("workaround_id") and other.get("breaks") == k["workaround_id"]:
            oc = conditions(other, env)
            if all(c["ok"] for c in oc):
                blockers.append(dict(ki=other["ki"], text=other["text"], conditions=oc))
    verdict = "✓" if applies and not blockers else "⚠" if applies else "?" if unknown else "✗"
    return dict(ki=k["ki"], id=k["id"], workaround=k["workaround"], status=k["status"], conditions=conds,
                applies=applies, blockers=blockers, verdict=verdict)


def show_eval(ev: dict, prefix: str = "    ") -> list[str]:
    head = {"✓": "applies", "⚠": "applies, but its workaround is forbidden here", "✗": "does not apply", "?": "unknown — ask the customer"}[ev["verdict"]]
    lines = [f"{prefix}{ev['verdict']} {ev['ki']}  {head}"]
    for c in ev["conditions"]:
        mark = "✓" if c["ok"] else "?" if c["ok"] is None else "✗"
        need = WORDS.get(c["want"], c["want"])
        got = f"{c['value']}  ({c['source']})" if c["value"] else "nothing on record"
        lines.append(f"{prefix}    {mark} {'needs ' + c['attr'].replace('_', ' ') + ': ' + need:<44} customer: {got}")
    for b in ev["blockers"]:
        lines.append(f"{prefix}    ⚠ its workaround ({clip(ev['workaround'], 50)}) is forbidden by {b['ki']}:")
        lines.append(f"{prefix}      “{clip(b['text'], 120)}”")
    return lines


def check_ticket(cli: CLI, ids: dict, story: dict, kis: dict, c: dict, text: str, symptom: str | None = None,
                 echo: bool = True) -> dict:
    """The memory-aware lookup: one search over known issues ∪ this customer's memory, then every known issue
    with the ticket's symptom checked against what the customer told us."""
    if symptom is None:
        top = next((h for h in symptom_search(cli, ids, kis, text, echo=echo) if h["ki"]), None)
        symptom = top and kis[top["ki"]]["symptom"]
    res = cli.run("search", text, "--projects", ids["ki"], "--actors", ids[c["key"]], "--types", "fact", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    env, other = environment(cli, ids[c["key"]], echo=echo)
    by_id = {k["id"]: k for k in kis.values()}
    env_ids = {e["id"]: a for a, e in env.items()}
    other_ids = {o["id"]: o for o in other}
    seen = []
    for f in res.get("facts") or []:
        fid = f.get("id")
        label = (f"known issue {by_id[fid]['ki']}" if fid in by_id else f"{c['short']}: {env_ids[fid]}" if fid in env_ids
                 else f"{c['short']}: {other_ids[fid]['kind']}" if fid in other_ids else "other")
        seen.append(dict(id=fid, text=f.get("fact") or f.get("content"), label=label))
    cands = sorted((k for k in kis.values() if k["symptom"] == symptom), key=lambda k: k["ki"])
    evals = [evaluate(k, env, kis) for k in cands]
    pick = next((e for e in evals if e["verdict"] == "✓"), None)
    contact = (env.get("contact") or {}).get("value")
    return dict(customer=c["key"], name=c["name"], text=text, symptom=symptom, search=seen, evals=evals,
                pick=pick and pick["ki"], reply=pick and pick["workaround"], contact=contact,
                env={a: dict(value=e["value"], ticket=e["ticket"], date=e["date"], id=e["id"]) for a, e in env.items()})


def show_check(r: dict, header: bool = True) -> None:
    lines = []
    if header:
        lines.append(f"\n  {r['name']} — `search` over known issues ∪ {r['name']}'s memory (top 8, one call):")
        lines += [f"    {i}. [{h['label']}] {clip(h['text'], 88)}" for i, h in enumerate(r["search"], 1)]
        lines.append(f"\n    Every known issue tagged {r['symptom']} (from `fact list`, not the top-k), checked against {r['name']}'s memory:")
    for ev in r["evals"]:
        lines += show_eval(ev)
    if r["pick"]:
        lines.append(f"    → reply: {r['pick']} — {r['reply']}" + (f"  (as {r['contact']})" if r["contact"] else ""))
    else:
        lines.append("    → no known issue fits this customer: escalate it as a new issue")
    emit("text", "\n".join(lines))


def with_memory(cli: CLI, ids: dict, story: dict, kis: dict, naive: dict) -> dict:
    note("now the same tickets, read together with each customer's memory")
    rows = []
    for c in story["customers"]:
        r = check_ticket(cli, ids, story, kis, c, c["today"]["text"], symptom=c["today"]["symptom"])
        show_check(r)
        n = next(x for x in naive["rows"] if x["customer"] == c["key"])
        ev = next((e for e in r["evals"] if e["ki"] == n["top"]), None)
        r["naive"] = dict(top=n["top"], right=bool(ev and ev["verdict"] == "✓"), eval=ev)
        if ev and ev["verdict"] != "✓":
            why = "; ".join(f"needs {x['attr'].replace('_', ' ')} {WORDS.get(x['want'], x['want'])}, {c['short']} has {x['value']} ({x['source']})"
                            for x in ev["conditions"] if x["ok"] is False)
            harm = "; ".join(f"and it would trigger {b['ki']}" for b in ev["blockers"])
            emit("text", f"    ✗ the symptom-only reply ({n['top']}) was wrong for {c['name']}: {why}" + (f" — {harm}" if harm else ""))
        elif ev:
            emit("text", f"    ✓ the symptom-only reply ({n['top']}) happens to be right for {c['name']}")
        rows.append(r)
    lines = [f"\n  {'ticket':<8} {'customer':<20} {'symptom only':<15} with customer memory"]
    for c, r in zip(story["customers"], rows):
        naive_ = f"{r['naive']['top'] or '—'} {'✓' if r['naive']['right'] else '✗'}"
        lines.append(f"  {c['today']['id']:<8} {r['name']:<20} {naive_:<15} {r['pick'] or 'escalate'} ✓")
    emit("text", "\n".join(lines))
    out = dict(rows=rows, wrong=sum(1 for r in rows if not r["naive"]["right"]))
    emit("checked", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: the reply goes into memory

def record_text(story: dict, c: dict, r: dict) -> str:
    t, leo = c["today"], story["agents"]["leo"]["name"]
    told = f"Matched {r['pick']}; told {c['name']}: {r['reply']}." if r["pick"] else "No known issue fits; escalated as a new issue."
    held = [e for e in r["evals"] if e["verdict"] in ("✗", "⚠")]
    why = []
    for e in held:
        bits = [f"{x['attr'].replace('_', ' ')} is {x['value']}" for x in e["conditions"] if x["ok"] is False]
        bits += [f"{b['ki']} forbids its workaround on this account" for b in e["blockers"]]
        why.append(f"{e['ki']} ({'; '.join(bits)})")
    return (f"Ticket {t['id']} ({story['today']}, {leo}): PDF invoice totals blank. {told}"
            + (f" Did not suggest {', '.join(why)}." if why else ""))


def come_back(cli: CLI, ids: dict, story: dict, c: dict, record_id: str | None, echo: bool = True) -> dict:
    q = story["come_back_question"]
    res = cli.run("search", q, "--actors", ids[c["key"]], "--types", "fact", "--top-k", "3", scoped=True, echo=echo) or {}
    hits = [dict(id=f.get("id"), text=f.get("fact") or f.get("content"), record=f.get("id") == record_id) for f in res.get("facts") or []]
    rank = next((i for i, h in enumerate(hits, 1) if h["record"]), None)
    return dict(question=q, hits=hits, rank=rank)


def remember_reply(cli: CLI, ids: dict, story: dict, checked: dict) -> dict:
    note("each reply goes onto the customer's actor: what was matched, what was said, what was held back and why")
    recs = []
    for c in story["customers"]:
        r = next(x for x in checked["rows"] if x["customer"] == c["key"])
        text = record_text(story, c, r)
        meta = dict(kind="ticket", ticket=c["today"]["id"], date=story["today"], agent=story["agents"]["leo"]["name"],
                    matched=r["pick"], withheld=[e["ki"] for e in r["evals"] if e["verdict"] in ("✗", "⚠")])
        got = pin(cli, "--actor", ids[c["key"]], [(text, meta)], "ticket, matched, withheld")
        recs.append(dict(customer=c["key"], name=c["name"], id=got[text]["id"], text=text, new=got[text]["new"], meta=meta))
        emit("text", f"\n  {c['name']}: {got[text]['id']}{'' if got[text]['new'] else ' (recorded by an earlier run)'}\n    “{text}”")
    c = story["customers"][-1]
    rec = next(x for x in recs if x["customer"] == c["key"])
    note(f"next week another agent opens {c['name']}'s next ticket: one `search` on the customer's actor")
    cb = come_back(cli, ids, story, c, rec["id"])
    lines = [f"\n  “{cb['question']}” — {c['name']}'s memory, top 3:"]
    lines += [f"    {i}. {'✓' if h['record'] else '·'} {clip(h['text'], 110)}" for i, h in enumerate(cb["hits"], 1)]
    lines.append(f"    today's record is #{cb['rank']}" if cb["rank"] else "    ✗ today's record is not in the top 3")
    emit("text", "\n".join(lines))
    out = dict(records=recs, come_back=dict(cb, customer=c["key"], name=c["name"]))
    emit("remembered", "", **out)
    return out


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
        # The binding takes a moment to show: `fact list` can still answer 404 right after it.
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


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for c in story["customers"]:
        conv = find(cli, "conv", f"ticket-{c['key']}", echo=not quiet)
        if conv:
            cli.run("conv", "delete", conv["id"], scoped=True)   # `proj delete` does not delete conversations
    for p in PROJECTS.values():
        proj = find(cli, "proj", p["key"], echo=not quiet)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
    for key in [c["key"] for c in story["customers"]] + [a["key"] for a in story["agents"].values()]:
        actor = find(cli, "actor", key, echo=not quiet)
        if actor:
            delete_actor(cli, actor["id"])
    note("deleted the two ticket conversations, both projects, both customers' memory and both support agents")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a known-issues project, a ticket-history project, one actor per customer")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Known issues — each pinned with when it applies and what its workaround breaks")
    kis = pin_known_issues(cli, ids, story)
    banner(4, TOTAL, "Customer memory — September's tickets, and what each customer told us")
    cm = customer_memory(cli, ids, story)
    banner(5, TOTAL, f"Today's tickets, by symptom only — the same blank PDF totals, twice ({story['today']})")
    naive = symptom_only(cli, ids, story, kis)
    banner(6, TOTAL, "The same tickets with customer memory — every condition checked, every source cited")
    checked = with_memory(cli, ids, story, kis, naive)
    banner(7, TOTAL, "The reply goes into memory — what was said, what was held back, found again next time")
    rem = remember_reply(cli, ids, story, checked)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   extracted={p["key"]: len(p["extracted"]) for p in cm["profiles"]}, tickets_project=cm["tickets_project"],
                   symptom_only={r["customer"]: r["top"] for r in naive["rows"]}, same=naive["same"],
                   ranks={r["customer"]: [h["ki"] for h in r["hits"]] for r in naive["rows"]},
                   checked={r["customer"]: r["pick"] for r in checked["rows"]},
                   naive_right={r["customer"]: r["naive"]["right"] for r in checked["rows"]},
                   blocked={r["customer"]: [b["ki"] for e in r["evals"] for b in e["blockers"]] for r in checked["rows"]},
                   come_back_rank=rem["come_back"]["rank"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    ids = {}
    for k, p in PROJECTS.items():
        proj = find(cli, "proj", p["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[k] = proj["id"]
    for c in story["customers"]:
        a = find(cli, "actor", c["key"], echo=False)
        if a is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[c["key"]] = a["id"]
    return ids


def ask(cli: CLI, ids: dict, key: str, text: str, echo: bool = True) -> dict:
    """Any ticket text, for one customer: the symptom-only answer and the memory-checked one, side by side."""
    story = load_story()
    c = customer(story, key)
    kis = {k["ki"]: k for k in known_issues(cli, ids, echo=False).values()}
    hits = symptom_search(cli, ids, kis, text, echo=echo)
    top = next((h for h in hits if h["ki"]), None)
    r = check_ticket(cli, ids, story, kis, c, text, symptom=top and kis[top["ki"]]["symptom"], echo=echo)
    ev = next((e for e in r["evals"] if top and e["ki"] == top["ki"]), None)
    r["naive"] = dict(top=top and top["ki"], hits=hits, right=bool(ev and ev["verdict"] == "✓"), eval=ev)
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "issues", "customer", "ask", "cleanup"],
                    help="run = full demo (default); issues = the known issues; customer NAME = what support knows about a "
                         "customer; ask NAME \"TICKET TEXT\" = symptom-only vs memory-checked for any ticket; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="customer (marlow / copperleaf) and, for `ask`, the ticket text")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "customer" and len(a.args) != 1:
        ap.error("customer needs a name: marlow or copperleaf")
    if a.command == "ask" and len(a.args) != 2:
        ap.error("ask needs a customer and the ticket text, e.g. ask copperleaf \"The VAT line is missing from my PDF\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py ask copperleaf \"Some VAT lines are missing on my PDF invoices\"`, "
              "`python3 demo.py customer marlow` or `python3 demo.py issues`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "issues":
        banner(2, 2, "Known issues")
        show_kis(list(known_issues(cli, ids).values()))
    elif a.command == "customer":
        c = customer(story, a.args[0])
        banner(2, 2, f"What support knows about {c['name']}")
        env, other = environment(cli, ids[c["key"]])
        show_profile(c, env, other)
    elif a.command == "ask":
        c = customer(story, a.args[0])
        banner(2, 2, f"Ask — a ticket from {c['name']}, by symptom only and with their memory")
        r = ask(cli, ids, c["key"], a.args[1])
        n = r["naive"]
        emit("text", "\n  By symptom only (known issues, top 3):\n"
                     + "\n".join(f"    {i}. {h['ki'] or '—'}  {clip(h['text'], 96)}" for i, h in enumerate(n["hits"], 1)))
        if r["symptom"] is None:
            emit("text", "\n  No known issue is close to this text: escalate it as a new issue.")
            return
        show_check(r)
        if n["top"]:
            emit("text", f"    {'✓' if n['right'] else '✗'} the symptom-only reply would have been {n['top']}")


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

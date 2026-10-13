#!/usr/bin/env python3
"""
Role-based access to AI memory, enforced by the server — a MemoryLake demo
driven entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Corvane Mutual (a fictional insurer) is rolling out three AI assistants on one
memory: a claims assistant, an underwriting bot and the external auditor's
reviewer. Its memory has three parts — the underwriting rule book, the open
claim files and the HR investigations file — and not every assistant may see,
let alone change, every part. The access policy is written down in
data/policy.json (assistant × memory → read / write / none).

The demo builds that for real and then tries to break it:

- a sandbox org team is created on your account (`team create
  --issue-api-key`); a personal team cannot have members, an org team can
- each assistant is a virtual member with its own API key (`member create`,
  `api-key create --member`), limited to the projects the policy names
  (`member scope set`) at the level it names (`access set-level`)
- every assistant then tries to read, search and write every part of memory
  with its own key: 27 calls, each one answered by the server — allowed, or
  403 with the permission it lacks — and compared with the policy
- `access show` lists who can reach the HR file and why
- one grant changes (`access grant` + `access set-level`): the next call
  with the same key is answered differently
- one assistant's key leaks: the member is removed and the key is refused
  on the next call
- `audit-log list` shows every one of those changes, who made it, and when

`cleanup` deletes the sandbox team (`team delete`), and with it everything
the demo created; your own team is never touched.

The companion web app (web/server.py) drives the same functions and streams the
same events. Requirements: Python 3.9+, the memorylake CLI on PATH, and a
MemoryLake API key of your personal team.
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
ORG_DIR = STATE_DIR / "org"            # the sandbox team's owner key, in its own CLI profile
MEMBERS_DIR = STATE_DIR / "members"    # one profile per assistant key
SANDBOX_FILE = STATE_DIR / "sandbox.json"  # the sandbox team's id and objects (no secrets)
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-rbc"  # memorylake-usecases / enterprise-rbac-memory

REFUSAL_WAIT = 30  # seconds a removed member's key is polled until the server refuses it

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

    `who` names the credential: "you" (the key you started the demo with), "sandbox owner" (the owner key of the
    sandbox team) or one of the assistants, each in its own isolated profile under .memorylake-demo/."""

    READ_ONLY = {"get", "list", "current", "search", "show", "levels", "explain", "me"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out", "error sending request",
                 "connection closed", "temporarily unavailable", "http 502", "http 503", "http 504", "internal_error")

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False, who: str = "you"):
        self.binary, self.env, self.show_json, self.who = binary, env, show_json, who
        self.workspace: str | None = None
        self.base_url = DEFAULT_BASE_URL

    def _echo(self, args: list[str]) -> None:
        line = shlex.join(["memorylake", *args])
        if self.who != "you":
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
        """One call whose refusal is an expected answer (401, 403, 409): rc, HTTP status, the server's message, the JSON.

        Transient network errors are retried, so that a refusal is always the server's answer, not a dropped line."""
        for i in range(5):
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and i == 0)
            if rc == 0 or not any(t in (err + out).lower() for t in self.TRANSIENT) or i == 4:
                break
            note(f"network hiccup; retry {i + 1}/4 in {2 * (i + 1)}s")
            time.sleep(2 * (i + 1))
        msg = server_message(err or out) if rc else ""
        body = None
        if rc == 0 and out.strip():
            try:
                body = json.loads(out)
            except json.JSONDecodeError:
                body = out
        return dict(rc=rc, status=http_status(err) if rc else 200, code=error_code(err) if rc else "", message=msg,
                    body=body)


def server_message(text: str) -> str:
    """The server's own sentence from a CLI error, without the request id."""
    lines = [ln.strip() for ln in mask(text).splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("{") or ln.startswith("Error:") or ln.startswith("Caused by") or ln.startswith("HTTP "):
            continue
        ln = re.sub(r"\s*\(request id: [^)]*\)", "", ln)
        return re.sub(r"\s*\[[A-Z_]+\]$", "", ln)
    return lines[0] if lines else ""


def http_status(text: str) -> int:
    m = re.search(r"HTTP (\d{3})", text or "")
    return int(m.group(1)) if m else 0


def error_code(text: str) -> str:
    m = re.search(r"\[([A-Z_]+)\]", text or "") or re.search(r'"error_code":"([A-Z_]+)"', text or "")
    return m.group(1) if m else ""


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
    """Log in with your key. `workspace` is accepted for symmetry with the other demos; everything this demo
    creates lives in the sandbox team's own default workspace."""
    binary = find_binary()
    env = dict(os.environ)
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    if api_key:
        STATE_DIR.mkdir(exist_ok=True)
        env["MEMORYLAKE_CONFIG_DIR"] = str(STATE_DIR)
        note(f"API key given — logging into an isolated profile under {STATE_DIR.name}/")
        cli.run("auth", "login", "--api-key", api_key, "--base-url", base_url, "--profile", PROFILE)
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
    cli.workspace = None
    cli.workspace_name = ""  # type: ignore[attr-defined]
    return cli


# --------------------------------------------------------------------------- data + helpers

def load_policy() -> dict:
    return json.loads((DATA / "policy.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def iso(ts: int | float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _pages(cli: CLI, args: list[str], echo: bool = True, scoped: bool = True, size: int = 100) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, "--page-size", str(size), *(["--continuation-token", token] if token else []),
                       scoped=scoped, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def read_sandbox() -> dict | None:
    try:
        return json.loads(SANDBOX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_sandbox(sb: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    SANDBOX_FILE.write_text(json.dumps(sb, indent=2) + "\n", encoding="utf-8")


def profile_cli(base: CLI, config_dir: Path, who: str, workspace: str | None) -> CLI:
    env = {k: v for k, v in base.env.items() if k != "MEMORYLAKE_API_KEY"}
    env["MEMORYLAKE_CONFIG_DIR"] = str(config_dir)
    c = CLI(base.binary, env, base.show_json, who=who)
    c.workspace, c.base_url = workspace, base.base_url
    return c


def login_profile(base: CLI, config_dir: Path, who: str, secret: str, workspace: str | None) -> CLI:
    """Store a key in its own profile; from here on the secret only lives in that profile's file."""
    if config_dir.exists():
        shutil.rmtree(config_dir)
    config_dir.mkdir(parents=True)
    c = profile_cli(base, config_dir, who, workspace)
    c.run("auth", "login", "--api-key", secret, "--base-url", base.base_url, "--profile", PROFILE)
    return c


def owner_cli(you: CLI, sb: dict) -> CLI:
    return profile_cli(you, ORG_DIR, "sandbox owner", sb.get("workspace"))


def member_cli(you: CLI, sb: dict, slug: str) -> CLI:
    return profile_cli(you, MEMBERS_DIR / slug, slug, sb.get("workspace"))


# --------------------------------------------------------------------------- step 2: sandbox team

def create_sandbox(you: CLI, policy: dict) -> dict:
    if (you.team or {}).get("type") != "personal":  # type: ignore[attr-defined]
        die("run this demo with an API key of your *personal* team: it creates a separate sandbox org team for "
            "the assistants and deletes it at the end, so nothing is added to a real org team")
    note("a personal team cannot have members; an org team can. The demo creates one just for this run")
    note("an account may own one org team besides its personal team: if yours already has one, this step stops")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    r = you.attempt("team", "create", "--name", policy["team_name"], "--issue-api-key",
                    "--api-key-name", cid("owner"), "--idempotency-key", f"{PREFIX}-{run_id}")
    if r["rc"] != 0:
        if r["code"] == "TENANT_CREATION_LIMIT_REACHED" or "limit" in r["message"].lower():
            die(f"MemoryLake answered: “{r['message']}”.\n"
                "  Your account already owns an org team, and an account may own one. Delete the org team you no "
                "longer need\n  (with its owner key: `memorylake team delete --confirm <team-id>`), or run the demo "
                "with the key of another account.")
        die(f"`team create` failed: HTTP {r['status']} {r['message']}")
    body = r["body"] or {}
    team, key = body.get("team") or {}, body.get("api_key") or {}
    secret = key.get("key") or ""
    sb = dict(team_id=team.get("id"), team_name=team.get("name"), created_at=team.get("created_at"),
              owner_key_id=key.get("id"), owner_key_prefix=key.get("key_prefix"), run_id=run_id, members={},
              projects={}, keys={})
    write_sandbox(sb)  # before anything else: the team id is what cleanup needs
    if not secret:
        die(f"`team create` returned no owner key for team {team.get('id')}; delete it in the MemoryLake console")
    owner = login_profile(you, ORG_DIR, "sandbox owner", secret, None)
    del secret
    note(f"org team {team.get('id')} “{team.get('name')}” — owner key {cid('owner')} (id {key.get('id')}) is "
         f"printed once; it now lives only in {ORG_DIR.relative_to(HERE)}/")
    t = owner.run("team", "get")
    items = (owner.run("ws", "list", "--page-size", "100") or {}).get("items") or []
    items.sort(key=lambda w: (w.get("custom_id") != "_sys_default_workspace", w.get("created_at") or ""))
    if not items:
        die("the sandbox team has no workspace")
    sb["workspace"], sb["workspace_name"] = items[0]["id"], items[0]["name"]
    write_sandbox(sb)
    note(f"sandbox team type “{t.get('type')}”, your role there “{t.get('caller_role')}”, "
         f"workspace “{sb['workspace_name']}” ({sb['workspace']})")
    emit("sandbox", "", team=dict(id=sb["team_id"], name=sb["team_name"], type=t.get("type"), role=t.get("caller_role")),
         workspace=dict(id=sb["workspace"], name=sb["workspace_name"]), owner_key=dict(
             id=sb["owner_key_id"], name=cid("owner"), prefix=sb["owner_key_prefix"]),
         personal=dict(name=you.team.get("name"), type=you.team.get("type")))  # type: ignore[attr-defined]
    return sb


# --------------------------------------------------------------------------- step 3: memory

def build_memory(you: CLI, sb: dict, policy: dict) -> dict:
    owner = owner_cli(you, sb)
    out = []
    for p in policy["projects"]:
        proj = owner.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["slug"]),
                         "--description", p["description"], scoped=True)
        sb["projects"][p["slug"]] = dict(id=proj["id"], name=p["name"])
        facts = (owner.run("fact", "add", "--project", proj["id"], *p["facts"], scoped=True) or {}).get("facts") or []
        if len(facts) != len(p["facts"]):
            die(f"fact add returned {len(facts)} of {len(p['facts'])} facts for {p['name']}")
        out.append(dict(slug=p["slug"], id=proj["id"], name=p["name"], description=p["description"],
                        facts=[f["fact"] for f in facts]))
        emit("text", f"\n  {p['name']} ({proj['id']}) — {len(facts)} facts\n  " + "\n  ".join(
            f"  {clip(f['fact'], 100)}" for f in facts))
    write_sandbox(sb)
    note("written with the sandbox owner's key; the assistants have no key yet")
    emit("memory", "", projects=out)
    return dict(projects=out)


# --------------------------------------------------------------------------- step 4: members, policy, keys

# What each level may do on a project. A scope instance without `actions` gets every action the role allows
# (a tenant_member may add memory anywhere), and levels only ever add: so each instance carries its actions.
ACTIONS = {
    "read": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search",
             "project:search"],
}
ACTIONS["write"] = ACTIONS["read"] + ["project:mem_add", "project:doc_add", "project:mem_conflict_read"]


def scope_entries(sb: dict, grants: dict) -> str:
    inst = [dict(id=sb["projects"][p]["id"], workspace_id=sb["workspace"], actions=ACTIONS[level])
            for p, level in grants.items()]
    return json.dumps([dict(resource_type="memorylake:project", mode="instances", instances=inst)])


def apply_policy(you: CLI, sb: dict, policy: dict) -> dict:
    owner = owner_cli(you, sb)
    ws = sb["workspace"]
    rows = []
    for m in policy["members"]:
        made = owner.run("member", "create", "--name", m["name"], "--role", "tenant_member")
        sb["members"][m["slug"]] = dict(id=made["principal_id"], name=m["name"])
        note(f"{m['name']}: virtual member {made['principal_id']} ({made.get('member_type')}, role {made.get('role')})")
    write_sandbox(sb)
    note("a tenant_member reaches every project by default — the policy limits each one to the projects it names,")
    note("with the actions of its level on each (scope), and records the level itself (access set-level)")
    for m in policy["members"]:
        pid = sb["members"][m["slug"]]["id"]
        grants = policy["grants"].get(m["slug"]) or {}
        reason = policy["reasons"][m["slug"]]
        owner.run("member", "scope", "set", pid, "--reason", reason, "--scopes", scope_entries(sb, grants))
        for p, level in grants.items():
            owner.run("access", "set-level", "--member", pid, "--level", level, "--reason", reason, "project",
                      sb["projects"][p]["id"], "--workspace", ws)
        rows.append(dict(member=m["slug"], principal_id=pid, purpose=m["purpose"], grants=grants, reason=reason))
    for m in policy["members"]:
        pid = sb["members"][m["slug"]]["id"]
        k = owner.run("api-key", "create", "--name", cid(m["slug"]), "--member", pid)
        secret = k.get("key") or ""
        if not secret:
            die(f"`api-key create --member` returned no key for {m['name']}")
        login_profile(you, MEMBERS_DIR / m["slug"], m["slug"], secret, ws)
        sb["keys"][m["slug"]] = dict(id=str(k["id"]), name=cid(m["slug"]), prefix=k.get("key_prefix"))
        note(f"{m['name']}: key {cid(m['slug'])} (id {k['id']}, {mask(secret)}) → its own profile "
             f".memorylake-demo/members/{m['slug']}/")
        del secret
    write_sandbox(sb)
    projects = [p["slug"] for p in policy["projects"]]
    names = {p["slug"]: p["name"] for p in policy["projects"]}
    emit("text", "\n  access policy (data/policy.json)\n  " + f"{'':<18}" + "".join(f"{names[p][:20]:<22}" for p in projects)
         + "\n  " + "\n  ".join(f"{r['member']:<18}" + "".join(f"{(r['grants'].get(p) or '—'):<22}" for p in projects)
                                for r in rows))
    emit("policy", "", members=rows, projects=[dict(slug=p, name=names[p]) for p in projects],
         keys={s: dict(name=v["name"], id=v["id"], prefix=v["prefix"]) for s, v in sb["keys"].items()})
    return dict(members=rows)


# --------------------------------------------------------------------------- step 5: the matrix

OPS = ("read", "search", "write")
ALLOWED = {"none": set(), "read": {"read", "search"}, "write": {"read", "search", "write"}}


def try_op(c: CLI, op: str, proj: dict, spec: dict, member: dict, echo: bool = True) -> dict:
    if op == "read":
        r = c.attempt("fact", "list", "--projects", proj["id"], "--page-size", "50", scoped=True, echo=echo)
        n = len(((r.get("body") or {}).get("items")) or []) if r["rc"] == 0 else None
        r["detail"] = f"{n} facts" if r["rc"] == 0 else ""
    elif op == "search":
        r = c.attempt("search", spec["query"], "--projects", proj["id"], "--top-k", "3", scoped=True, echo=echo)
        hits = ((r.get("body") or {}).get("facts")) or [] if r["rc"] == 0 else []
        r["detail"] = f"#1 {clip(hits[0].get('content') or hits[0].get('fact') or '', 60)}" if hits else (
            "0 hits" if r["rc"] == 0 else "")
    else:
        r = c.attempt("fact", "add", "--project", proj["id"], member["note"], scoped=True, echo=echo)
        made = ((r.get("body") or {}).get("facts")) or [] if r["rc"] == 0 else []
        r["fact_id"] = made[0]["id"] if made else None
        r["detail"] = f"added {r['fact_id']}" if made else ""
    return r


def matrix_step(you: CLI, sb: dict, policy: dict) -> dict:
    note("every assistant now tries to read, search and write every part of memory — with its own key")
    cells, members = [], {m["slug"]: m for m in policy["members"]}
    for m in policy["members"]:
        c = member_cli(you, sb, m["slug"])
        grants = policy["grants"].get(m["slug"]) or {}
        for spec in policy["projects"]:
            proj = sb["projects"][spec["slug"]]
            level = grants.get(spec["slug"], "none")
            for op in OPS:
                r = try_op(c, op, proj, spec, members[m["slug"]])
                expected = op in ALLOWED[level]
                allowed = r["rc"] == 0
                if allowed and op == "write" and r.get("fact_id"):
                    # the server records no writer on a fact: the assistant stamps its own name (self-reported)
                    c.attempt("fact", "update", r["fact_id"], "--project", proj["id"], "--metadata",
                              json.dumps({"written_by": m["slug"]}), scoped=True, echo=False)
                cell = dict(member=m["slug"], project=spec["slug"], op=op, level=level, expected=expected,
                            allowed=allowed, status=r["status"], code=r["code"], message=r["message"],
                            detail=r["detail"], match=allowed == expected)
                cells.append(cell)
                emit("cell", "", **cell)
                if not cell["match"]:
                    note(f"MISMATCH: {m['slug']} {op} {spec['slug']} — policy says "
                         f"{'allow' if expected else 'deny'}, the server {'allowed it' if allowed else 'refused it'}")
    ok = sum(c["match"] for c in cells)
    names = {p["slug"]: p["name"] for p in policy["projects"]}
    lines = []
    for m in policy["members"]:
        row = []
        for spec in policy["projects"]:
            cs = [c for c in cells if c["member"] == m["slug"] and c["project"] == spec["slug"]]
            row.append(" ".join(f"{c['op'][0].upper()}{'✓' if c['allowed'] else '✗'}" + ("" if c["match"] else "!")
                                for c in cs))
        lines.append(f"{m['slug']:<18}" + "".join(f"{x:<22}" for x in row))
    emit("text", "\n  what the server allowed (R read · S search · W write; ✓ allowed, ✗ refused, ! differs from the policy)\n  "
         + f"{'':<18}" + "".join(f"{names[p['slug']][:20]:<22}" for p in policy["projects"]) + "\n  " + "\n  ".join(lines))
    refusals = sorted({(c["status"], c["code"]) for c in cells if not c["allowed"]})
    sample = next((c for c in cells if not c["allowed"]), None)
    note(f"{ok}/{len(cells)} calls answered as the policy says; refusals: "
         + ", ".join(f"HTTP {s} {code}" for s, code in refusals))
    if sample:
        note(f"e.g. {sample['member']} → {sample['op']} {names[sample['project']]}: “{sample['message']}”")
    out = dict(cells=cells, matched=ok, total=len(cells), refusals=[dict(status=s, code=c) for s, c in refusals])
    emit("matrix", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: who can reach HR

def who_step(you: CLI, sb: dict, policy: dict) -> dict:
    owner = owner_cli(you, sb)
    hr = sb["projects"]["hr"]
    shown = owner.run("access", "show", "project", hr["id"], "--workspace", sb["workspace"]) or {}
    allowed = [dict(name=a.get("name"), level=a.get("permission_level"), role=a.get("role"),
                    scope=a.get("scope_mode"), sources=[s.get("type") for s in a.get("sources") or []])
               for a in shown.get("allowed") or []]
    denied_raw = shown.get("blocked") or []
    denied = [dict(name=a.get("name"), role=a.get("role"), reason=a.get("reason") or a.get("why") or "")
              for a in denied_raw]
    emit("text", f"\n  access show project {hr['id']}  ({hr['name']})\n  can reach:\n  " + "\n  ".join(
        f"  {a['name']:<18} {a['level'] or '—':<7} role {a['role']:<14} via {', '.join(a['sources'])}" for a in allowed)
         + ("\n  cannot:\n  " + "\n  ".join(f"  {d['name']:<18} role {d['role']}" + (f" — {d['reason']}" if d["reason"] else "")
                                            for d in denied) if denied else ""))
    expected = {"audit-reviewer"}
    assistants = {a["name"] for a in allowed if a["name"] in sb["members"]}
    ok = assistants == expected
    note(f"assistants that reach the HR file: {', '.join(sorted(assistants)) or 'none'} — "
         f"{'as the policy says' if ok else 'NOT what the policy says'}")
    out = dict(project=hr, allowed=allowed, denied=denied, ok=ok, fields=sorted(shown.keys()))
    emit("who", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: change one grant

def effective(owner: CLI, sb: dict, project_id: str, name: str, echo: bool = True) -> dict:
    """What `access show` says a member reaches on a project: the effective level and where it comes from."""
    shown = owner.run("access", "show", "project", project_id, "--workspace", sb["workspace"], echo=echo) or {}
    a = next((x for x in shown.get("allowed") or [] if x.get("name") == name), None)
    if not a:
        return dict(level=None, sources=[])
    return dict(level=a.get("permission_level"),
                sources=[dict(type=s.get("type"), levels=s.get("permission_levels"), actions=s.get("actions"))
                         for s in a.get("sources") or []])


def fmt_sources(e: dict) -> str:
    return ", ".join(f"{s['type']}" + (f" ({'/'.join(s['levels'])})" if s.get("levels") else "")
                     + (" [every action the role allows]" if s.get("actions") == ["*"] and s["type"] == "scope_grant" else "")
                     for s in e["sources"]) or "—"


def change_step(you: CLI, sb: dict, policy: dict) -> dict:
    ch = policy["change"]
    owner = owner_cli(you, sb)
    c = member_cli(you, sb, ch["member"])
    spec = next(p for p in policy["projects"] if p["slug"] == ch["project"])
    proj = sb["projects"][ch["project"]]
    member = next(m for m in policy["members"] if m["slug"] == ch["member"])
    pid = sb["members"][ch["member"]]["id"]
    note(f"ticket {ch['reason'].split(':')[0]}: {ch['member']} needs {ch['level']} access to {proj['name']}")
    before = try_op(c, "search", proj, spec, member)
    note(f"before: HTTP {before['status']} “{before['message']}”" if before["rc"] else "before: already allowed")
    note("the quick way: add the project to its scope, set the level")
    owner.run("access", "grant", "--member", pid, "--reason", ch["reason"], "project", proj["id"],
              "--workspace", sb["workspace"])
    owner.run("access", "set-level", "--member", pid, "--level", ch["level"], "--reason", ch["reason"], "project",
              proj["id"], "--workspace", sb["workspace"])
    t0 = time.time()
    after = try_op(c, "search", proj, spec, member)
    took = round(time.time() - t0, 1)
    note(f"after, same key, next call: {'rc 0 — ' + after['detail'] if after['rc'] == 0 else 'HTTP ' + str(after['status']) + ' ' + after['message']}")
    write1 = try_op(c, "write", proj, spec, member)
    note(f"and a write, which the ticket did not ask for: "
         + (f"rc 0 — {write1['detail']}" if write1["rc"] == 0 else f"HTTP {write1['status']} “{write1['message']}”"))
    eff1 = effective(owner, sb, proj["id"], ch["member"])
    note(f"access show: {ch['member']} reaches {proj['name']} at level “{eff1['level']}”, from {fmt_sources(eff1)}")
    over = eff1["level"] not in (None, ch["level"]) or write1["rc"] == 0
    if over:
        note("levels only add: `access grant` put the project in its scope with every action the role allows, "
             "and a tenant_member may add memory. The level says read; the scope says write")
    note("the exact way: the scope entry carries the actions of the level")
    grants = dict(policy["grants"].get(ch["member"]) or {})
    grants[ch["project"]] = ch["level"]
    owner.run("member", "scope", "set", pid, "--reason", ch["reason"], "--scopes", scope_entries(sb, grants))
    write2 = try_op(c, "write", proj, spec, member)
    search2 = try_op(c, "search", proj, spec, member, echo=False)
    eff2 = effective(owner, sb, proj["id"], ch["member"])
    note(f"write now: " + (f"HTTP {write2['status']} “{write2['message']}”" if write2["rc"] else "rc 0 (still allowed!)")
         + f" · search: {'rc 0' if search2['rc'] == 0 else 'HTTP ' + str(search2['status'])}")
    note(f"access show: level “{eff2['level']}”, from {fmt_sources(eff2)}")
    if write1.get("fact_id"):
        owner.run("fact", "delete", "--project", proj["id"], write1["fact_id"], scoped=True)
        note(f"the note it was never meant to write is deleted ({write1['fact_id']})")
    policy["grants"].setdefault(ch["member"], {})[ch["project"]] = ch["level"]
    ok = before["rc"] != 0 and after["rc"] == 0 and write2["rc"] != 0 and search2["rc"] == 0
    out = dict(member=ch["member"], project=proj, level=ch["level"], reason=ch["reason"],
               before=dict(status=before["status"], message=before["message"]),
               after=dict(rc=after["rc"], detail=after["detail"], status=after["status"], message=after["message"]),
               quick=dict(write_rc=write1["rc"], write_status=write1["status"], write_message=write1["message"],
                          effective=eff1),
               over_granted=over,
               exact=dict(write_rc=write2["rc"], write_status=write2["status"], write_message=write2["message"],
                          search_rc=search2["rc"], effective=eff2),
               seconds=took, ok=ok)
    emit("change", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: remove a member

def remove_step(you: CLI, sb: dict, policy: dict) -> dict:
    inc = policy["incident"]
    owner = owner_cli(you, sb)
    c = member_cli(you, sb, inc["member"])
    proj = sb["projects"]["claims"]
    pid = sb["members"][inc["member"]]["id"]
    note(f"incident {inc['reason']}")
    before = c.attempt("fact", "list", "--projects", proj["id"], "--page-size", "50", scoped=True)
    n_before = len(((before.get("body") or {}).get("items")) or []) if before["rc"] == 0 else None
    note(f"{inc['member']} reads the claim files: {'rc 0, ' + str(n_before) + ' facts' if before['rc'] == 0 else before['message']}")
    owner.run("member", "remove", pid)
    t0, first = time.time(), True
    while True:
        r = c.attempt("fact", "list", "--projects", proj["id"], "--page-size", "50", scoped=True, echo=first)
        first = False
        after_s = round(time.time() - t0, 1)
        if r["rc"] != 0 or after_s > REFUSAL_WAIT:
            break
        time.sleep(1)
    note(f"the same call {after_s}s later: " + (f"HTTP {r['status']} “{r['message']}”" if r["rc"] else "STILL ACCEPTED"))
    written = [f for f in _pages(owner, ["fact", "list", "--projects", proj["id"]], echo=False)
               if (f.get("metadata") or {}).get("written_by") == inc["member"]]
    note(f"what it wrote stays in the claim files: {len(written)} fact(s) with written_by={inc['member']}")
    roster = owner.run("member", "list") or {}
    names = [x.get("display_name") for x in roster.get("items") or []]
    note(f"team roster now: {', '.join(names)}")
    shutil.rmtree(MEMBERS_DIR / inc["member"], ignore_errors=True)
    sb["members"][inc["member"]]["removed"] = True
    write_sandbox(sb)
    ok = before["rc"] == 0 and r["rc"] != 0
    out = dict(member=inc["member"], reason=inc["reason"], before=n_before,
               refused=dict(rc=r["rc"], status=r["status"], code=r["code"], message=r["message"], after_s=after_s),
               kept=len(written), roster=names, ok=ok)
    emit("remove", "", **out)
    return out


# --------------------------------------------------------------------------- step 9: audit log

def audit_rows(owner: CLI, since: int | None, echo: bool = True) -> list[dict]:
    items = (owner.run("audit-log", "list", "--page-size", "100", echo=echo) or {}).get("items") or []
    rows = []
    for e in items:
        if since and (e.get("created_at") or 0) < since:
            continue
        a, t = e.get("actor") or {}, e.get("target") or {}
        rows.append(dict(at=e.get("created_at"), action=e.get("action"), result=e.get("result"),
                         by=a.get("display_name"), key=a.get("api_key_id"), target=t.get("label") or t.get("id"),
                         target_type=t.get("type"), reason=e.get("reason"), fields=sorted(e.keys())))
    rows.sort(key=lambda r: (r["at"] or 0, r["action"] or ""))
    return rows


def audit_step(you: CLI, sb: dict, policy: dict, echo: bool = True) -> dict:
    owner = owner_cli(you, sb)
    rows = audit_rows(owner, sb.get("created_at"), echo=echo)
    emit("text", "\n  " + f"{'time (UTC)':<21}{'action':<28}{'by':<12}{'key':<8}target\n  " + "\n  ".join(
        f"{iso(r['at']):<21}{r['action'] or '':<28}{(r['by'] or '')[:11]:<12}{str(r['key'] or ''):<8}{r['target'] or ''}"
        for r in rows))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    fields = sorted({f for r in rows for f in r["fields"]})
    has_reason = any(r["reason"] for r in rows)
    note(f"{len(rows)} event(s) since the sandbox was created: " + ", ".join(f"{k} ×{v}" for k, v in counts.items()))
    note("each names the key that made the change (actor.api_key_id) and its target; "
         + ("the --reason text is recorded" if has_reason else
            "the --reason given to scope / level changes is not in the event (fields: " + ", ".join(fields) + ")"))
    data_events = [r for r in rows if r["action"] and r["action"].split(":")[0] in ("fact", "memory", "search", "conversation")]
    note(f"data-plane events (fact add, search) in the log: {len(data_events)} — it records "
         "who changed access, not who read memory")
    out = dict(rows=[{k: v for k, v in r.items() if k != "fields"} for r in rows], counts=counts, fields=fields,
               has_reason=has_reason, data_events=len(data_events))
    emit("audit", "", **out)
    return out


# --------------------------------------------------------------------------- report

def write_report(policy: dict, sb: dict, res: dict) -> dict:
    names = {p["slug"]: p["name"] for p in policy["projects"]}
    doc = dict(company=policy["company"]["name"], generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               sandbox=dict(team_id=sb["team_id"], team_name=sb["team_name"], workspace=sb.get("workspace")),
               policy=policy["grants"], matrix=res["matrix"], who=res["who"], change=res["change"],
               remove=res["remove"], audit=res["audit"])
    OUT.mkdir(exist_ok=True)
    (OUT / "access-review.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    m = res["matrix"]
    lines = [f"# {policy['company']['name']} — AI memory access review", "",
             f"Generated {doc['generated_at']} against a sandbox org team ({sb['team_id']}). Every cell below is a real "
             "call made with that assistant's own API key; the server's answer is recorded as given.", "",
             f"**{m['matched']}/{m['total']} calls answered as the policy says.**", "",
             "| assistant | project | level | read | search | write |", "|---|---|---|---|---|---|"]
    for mem in policy["members"]:
        for p in policy["projects"]:
            cs = {c["op"]: c for c in m["cells"] if c["member"] == mem["slug"] and c["project"] == p["slug"]}
            lvl = next(iter(cs.values()))["level"]
            fmt = lambda c: ("✓" if c["allowed"] else f"✗ {c['status']}") + ("" if c["match"] else " (differs)")
            lines.append(f"| {mem['slug']} | {names[p['slug']]} | {lvl} | {fmt(cs['read'])} | {fmt(cs['search'])} | {fmt(cs['write'])} |")
    refused = [c for c in m["cells"] if not c["allowed"]]
    if refused:
        lines += ["", "Server messages for the refusals:", ""]
        for msg in sorted({c["message"] for c in refused}):
            lines.append(f"- {msg}")
    ch, rm = res["change"], res["remove"]
    lines += ["", "## Change", "", f"{ch['reason']}: before HTTP {ch['before']['status']} · after `access grant` + "
              f"`set-level {ch['level']}`: search {'rc 0' if ch['after']['rc'] == 0 else 'HTTP ' + str(ch['after']['status'])}, "
              f"write {'rc 0' if ch['quick']['write_rc'] == 0 else 'HTTP ' + str(ch['quick']['write_status'])} "
              f"(access show: {ch['quick']['effective']['level']}) · after `member scope set` with the level's actions: "
              f"write {'rc 0' if ch['exact']['write_rc'] == 0 else 'HTTP ' + str(ch['exact']['write_status'])} "
              f"(access show: {ch['exact']['effective']['level']})", "", "## Removal", "",
              f"{rm['reason']}: refused {rm['refused']['after_s']} s after `member remove` — HTTP {rm['refused']['status']} "
              f"“{rm['refused']['message']}”; {rm['kept']} fact(s) it wrote stay.", "", "## Audit log", "",
              "| time (UTC) | action | by | key | target |", "|---|---|---|---|---|"]
    for r in res["audit"]["rows"]:
        lines.append(f"| {iso(r['at'])} | {r['action']} | {r['by']} | {r['key']} | {r['target']} |")
    (OUT / "access-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return doc


def show_report() -> dict:
    p = OUT / "access-review.json"
    if not p.exists():
        die("no access review yet; run `python3 demo.py` first")
    doc = json.loads(p.read_text(encoding="utf-8"))
    m = doc["matrix"]
    emit("text", f"\n  {doc['company']} — AI memory access review ({doc['generated_at']})\n"
                 f"  {m['matched']}/{m['total']} calls answered as the policy says\n  full report: out/access-review.md")
    emit("report", "", **doc)
    return doc


# --------------------------------------------------------------------------- cleanup

def cleanup(you: CLI, quiet: bool = False) -> None:
    sb = read_sandbox()
    if not sb or not sb.get("team_id"):
        note("no sandbox team recorded in .memorylake-demo/ — nothing to delete")
    else:
        owner = owner_cli(you, sb)
        r = owner.attempt("team", "delete", "--confirm", sb["team_id"])
        if r["rc"] == 0:
            note(f"deleted the sandbox team {sb['team_id']} — its projects, members and keys go with it")
            gone = owner.attempt("team", "get", echo=not quiet)
            note(f"its owner key now: {'HTTP ' + str(gone['status']) + ' “' + gone['message'] + '”' if gone['rc'] else 'still works'}")
        elif r["status"] in (401, 404):
            note(f"the sandbox team {sb['team_id']} is already gone (HTTP {r['status']} {r['message']})")
        else:
            die(f"could not delete the sandbox team {sb['team_id']}: HTTP {r['status']} {r['message']}\n"
                "  its owner key is in .memorylake-demo/org/ — nothing local was removed, run cleanup again")
    for d in (ORG_DIR, MEMBERS_DIR):
        shutil.rmtree(d, ignore_errors=True)
    SANDBOX_FILE.unlink(missing_ok=True)
    note("deleted the sandbox and assistant profiles under .memorylake-demo/ (your own login stays)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(you: CLI, reset: bool = False) -> dict:
    policy = load_policy()
    t0 = time.time()
    emit("intro", "", company=policy["company"], members=policy["members"],
         projects=[dict(slug=p["slug"], name=p["name"], description=p["description"]) for p in policy["projects"]],
         grants=policy["grants"])
    banner(2, TOTAL, "A sandbox org team — the only kind of team that can have members")
    if reset or read_sandbox():
        note("--reset" if reset else "found a sandbox team from an earlier run")
        note("teams, members and keys are one-shot, so every run starts from nothing: deleting it first")
        cleanup(you, quiet=True)
    sb = create_sandbox(you, policy)
    banner(3, TOTAL, "Corvane's memory — rule book, claim files, HR investigations")
    build_memory(you, sb, policy)
    banner(4, TOTAL, "One member per assistant, limited by the policy, each with its own key")
    apply_policy(you, sb, policy)
    banner(5, TOTAL, "27 calls: every assistant × every project × read / search / write")
    mx = matrix_step(you, sb, policy)
    banner(6, TOTAL, "Who can reach the HR file — and why")
    who = who_step(you, sb, policy)
    banner(7, TOTAL, "A grant changes — and the quick way grants more than the ticket asked")
    ch = change_step(you, sb, policy)
    banner(8, TOTAL, "A key leaks: remove the member, the key is refused")
    rm = remove_step(you, sb, policy)
    banner(9, TOTAL, "The audit log: every access change, who made it, when")
    au = audit_step(you, sb, policy)
    write_report(load_policy(), sb, dict(matrix=mx, who=who, change=ch, remove=rm, audit=au))
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   matched=mx["matched"], total=mx["total"], refusals=mx["refusals"], who_ok=who["ok"],
                   change_ok=ch["ok"], change_s=ch["seconds"], over_granted=ch["over_granted"],
                   quick_level=ch["quick"]["effective"]["level"], exact_level=ch["exact"]["effective"]["level"], remove_ok=rm["ok"], remove_s=rm["refused"]["after_s"],
                   remove_status=rm["refused"]["status"], audit_events=len(au["rows"]), audit_counts=au["counts"],
                   audit_reason=au["has_reason"])
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    emit("summary", "", **summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "report", "audit", "cleanup"],
                    help="run = full demo (default); report = print the last access review; audit = read the sandbox "
                         "team's audit log again; cleanup = delete the sandbox team and its local profiles")
    ap.add_argument("--reset", action="store_true", help="delete a sandbox team left by an earlier run first (a run "
                                                         "does this on its own when it finds one)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (keys masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "report":
        show_report()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — your API key, your personal team")
    you = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — delete the sandbox team")
        cleanup(you)
        return
    if args.command == "audit":
        banner(2, 2, "The sandbox team's audit log")
        sb = read_sandbox()
        if not sb:
            die("no sandbox team — run `python3 demo.py` first (cleanup deletes it, and its audit log with it)")
        audit_step(you, sb, load_policy())
        return
    run_pipeline(you, reset=args.reset)
    print("\nDone. The sandbox team is still there so you can look around: `python3 demo.py audit` reads its audit "
          "log again,\n`python3 demo.py report` prints the access review (out/access-review.md), "
          "`python3 demo.py cleanup` deletes the sandbox team.")


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

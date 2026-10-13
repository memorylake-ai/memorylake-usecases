#!/usr/bin/env python3
"""
A team second brain with role-gated access — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Thornfield Field Ecology Lab (fictional) keeps its institutional knowledge in
one shared MemoryLake memory: the methods and decisions behind its surveys,
the running field notes of the active studies, and the confidential funder
reports. Mira, its most senior researcher, leaves at the end of the month;
Theo joined this week. Who may read and change what is written down in
data/team.json.

The demo builds that access model for real and then checks it:

- a sandbox org team is created on your account (`team create
  --issue-api-key`); a personal team cannot have members, an org team can
- a custom role, "Lab researcher", is defined from nothing with permission
  rules (`role create`, `permission rule create`) and limited to two of the
  three projects, read-only, with `role scope set`
- three access groups add what the role does not give: the field crew adds
  field notes, the method stewards change the methods, the grant team alone
  sees the funder reports (`access-group create / scope set / member add`);
  `access-group explain` and `member explain` say where each permission
  comes from
- every researcher writes their part of the second brain with their own key,
  then tries to read, search and write every project: 27 calls, each one
  answered by the server and compared with the model
- a tidy-up of the role scope (dropping the per-project action lists the help
  calls optional) lets the field crew's write permission land on the methods
  too; putting the actions back closes it again
- Mira leaves (`member remove`): her key is refused on the next call, and
  what she wrote is still the first answer Theo gets
- `audit-log list` shows the role, group and member changes

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
MEMBERS_DIR = STATE_DIR / "members"    # one profile per researcher key
SANDBOX_FILE = STATE_DIR / "sandbox.json"  # the sandbox team's id and objects (no secrets)
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tsb"  # memorylake-usecases / team-second-brain

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
    sandbox team) or one of the researchers, each in its own isolated profile under .memorylake-demo/."""

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

def load_team() -> dict:
    return json.loads((DATA / "team.json").read_text(encoding="utf-8"))


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


def short_perm(p: str) -> str:
    """memorylake:project:project:mem_add → project:mem_add"""
    parts = (p or "").split(":")
    return ":".join(parts[-2:]) if len(parts) >= 2 else p


def expected_level(team: dict, member: dict, project: str) -> str:
    """What the access model in data/team.json says a member may do on a project."""
    groups = {g["slug"]: g for g in team["groups"]}
    if any(groups[g]["project"] == project for g in member["groups"]):
        return "write"
    return "read" if project in team["role"]["scope"] else "none"


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

def create_sandbox(you: CLI, team: dict) -> dict:
    if (you.team or {}).get("type") != "personal":  # type: ignore[attr-defined]
        die("run this demo with an API key of your *personal* team: it creates a separate sandbox org team for "
            "the researchers and deletes it at the end, so nothing is added to a real org team")
    note("a personal team cannot have members; an org team can. The demo creates one just for this run")
    note("an account may own one org team besides its personal team: if yours already has one, this step stops")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    r = you.attempt("team", "create", "--name", team["team_name"], "--issue-api-key",
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



# --------------------------------------------------------------------------- step 3: a custom role

# What each level may do on a project. A scope instance without `actions` gets every action the member holds
# from anywhere — role or access group — so every instance carries its actions (step 7 shows why).
ACTIONS = {
    "read": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search",
             "project:search"],
}
ACTIONS["write"] = ACTIONS["read"] + ["project:mem_add"]


def role_scope(sb: dict, team: dict, with_actions: bool = True) -> str:
    inst = []
    for p in team["role"]["scope"]:
        i = dict(id=sb["projects"][p]["id"], workspace_id=sb["workspace"])
        if with_actions:
            i["actions"] = ACTIONS["read"]
        inst.append(i)
    return json.dumps([dict(resource_type="memorylake:project", mode="instances", instances=inst)])


def define_role(you: CLI, sb: dict, team: dict) -> dict:
    owner = owner_cli(you, sb)
    for p in team["projects"]:
        proj = owner.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["slug"]),
                         "--description", p["description"], scoped=True)
        sb["projects"][p["slug"]] = dict(id=proj["id"], name=p["name"])
    write_sandbox(sb)
    note("three empty projects; the researchers fill them in step 5, each with their own key")
    r = team["role"]
    role = owner.run("role", "create", "--label", r["label"], "--description", r["description"],
                     "--idempotency-key", f"{PREFIX}-{sb['run_id']}-role")
    sb["role"] = dict(key=role["key"], label=role.get("label"))
    write_sandbox(sb)
    note(f"custom role “{role.get('label')}” → key {role['key']}, permissions {len(role.get('permissions') or [])} "
         f"(a custom role starts with nothing)")
    for rule in r["rules"]:
        owner.run("permission", "rule", "create", "--role", role["key"], "--resource", rule["resource"],
                  "--action", ",".join(rule["actions"]), "--reason", r["reason"])
    note("search needs a workspace permission as well as the project ones (workspace:memory_search)")
    owner.run("role", "scope", "set", role["key"], "--reason", r["reason"], "--scopes", role_scope(sb, team))
    got = owner.run("role", "get", role["key"]) or {}
    perms = sorted(short_perm(f"{p.get('resource_type')}:{p.get('action')}") if p.get("action") else
                   short_perm(p.get("permission") or "") for p in got.get("permissions") or [])
    scope = owner.run("role", "scope", "get", role["key"]) or {}
    proj_scope = next((s for s in scope.get("scopes") or [] if s.get("resource_type") == "memorylake:project"), {})
    names = {v["id"]: k for k, v in sb["projects"].items()}
    reach = [dict(project=names.get(i.get("id"), i.get("id")), actions=i.get("actions") or ["(every action)"])
             for i in proj_scope.get("instances") or []]
    emit("text", f"\n  role {role['key']} “{role.get('label')}”\n  permissions: {', '.join(perms)}\n"
                 f"  project scope: {proj_scope.get('mode')} — " + "; ".join(
                     f"{x['project']} ({len(x['actions'])} actions, read-only)" for x in reach)
         + f"\n  not in scope: " + ", ".join(p["slug"] for p in team["projects"] if p["slug"] not in r["scope"]))
    out = dict(key=role["key"], label=role.get("label"), permissions=perms, scope_mode=proj_scope.get("mode"),
               reach=reach, reason=r["reason"])
    emit("role", "", **out)
    return out


# --------------------------------------------------------------------------- step 4: members, groups, keys

def group_permissions() -> list[str]:
    return [f"memorylake:project:{a}" for a in ACTIONS["write"]]


def define_groups(you: CLI, sb: dict, team: dict) -> dict:
    owner = owner_cli(you, sb)
    ws = sb["workspace"]
    for m in team["members"]:
        made = owner.run("member", "create", "--name", m["name"][:20], "--role", sb["role"]["key"])
        sb["members"][m["slug"]] = dict(id=made["principal_id"], name=m["name"])
        note(f"{m['name']} ({m['title']}): virtual member {made['principal_id']}, role {made.get('role')}")
    write_sandbox(sb)
    sb["groups"] = {}
    rows = []
    for g in team["groups"]:
        made = owner.run("access-group", "create", "--name", g["name"], "--description", g["description"],
                         *[x for p in group_permissions() for x in ("--permission", p)],
                         "--idempotency-key", f"{PREFIX}-{sb['run_id']}-{g['slug']}")
        gid = made.get("group_id") or made.get("id")
        sb["groups"][g["slug"]] = dict(id=gid, name=g["name"])
        write_sandbox(sb)
        proj = sb["projects"][g["project"]]
        owner.run("access-group", "scope", "set", gid, "--reason", g["reason"], "--scopes", json.dumps(
            [dict(resource_type="memorylake:project", mode="instances",
                  instances=[dict(id=proj["id"], workspace_id=ws)])]))
        ids = [sb["members"][m["slug"]]["id"] for m in team["members"] if g["slug"] in m["groups"]]
        owner.run("access-group", "member", "add", gid, *ids)
        rows.append(dict(slug=g["slug"], id=gid, name=g["name"], project=g["project"],
                         members=[m["slug"] for m in team["members"] if g["slug"] in m["groups"]]))
    note("each group adds write on one project — and, through its scope, reach to that project")
    ex = owner.run("access-group", "explain", sb["groups"]["grant-team"]["id"]) or {}
    gscope = next((s for s in ex.get("scopes") or [] if s.get("resource_type") == "memorylake:project"), {})
    names = {v["id"]: k for k, v in sb["projects"].items()}
    emit("text", f"\n  access-group explain “{ex.get('name')}” — {ex.get('member_count')} member(s)\n  permissions: "
         + ", ".join(short_perm(p.get("permission")) for p in ex.get("permissions") or [])
         + "\n  projects it opens: " + ", ".join(names.get(i.get("id"), i.get("id")) for i in gscope.get("instances") or []))
    for m in team["members"]:
        k = owner.run("api-key", "create", "--name", cid(m["slug"]), "--member", sb["members"][m["slug"]]["id"])
        secret = k.get("key") or ""
        if not secret:
            die(f"`api-key create --member` returned no key for {m['name']}")
        login_profile(you, MEMBERS_DIR / m["slug"], m["slug"], secret, ws)
        sb["keys"][m["slug"]] = dict(id=str(k["id"]), name=cid(m["slug"]), prefix=k.get("key_prefix"))
        note(f"{m['name']}: key {cid(m['slug'])} (id {k['id']}, {mask(secret)}) → its own profile "
             f".memorylake-demo/members/{m['slug']}/")
        del secret
    write_sandbox(sb)
    expl = explain_member(owner, sb, "mira")
    emit("text", "\n  member explain Mira — where each project permission comes from\n  " + "\n  ".join(
        f"  {p['permission']:<22} {', '.join(p['sources'])}" for p in expl["permissions"])
         + "\n  projects she reaches\n  " + "\n  ".join(f"  {r['project']:<22} via {', '.join(r['sources'])}"
                                                    for r in expl["reach"]))
    model = {m["slug"]: {p["slug"]: expected_level(team, m, p["slug"]) for p in team["projects"]} for m in team["members"]}
    projects = [p["slug"] for p in team["projects"]]
    emit("text", "\n  the access model (data/team.json): role + groups\n  " + f"{'':<8}" + "".join(f"{p:<16}" for p in projects)
         + "\n  " + "\n  ".join(f"{m:<8}" + "".join(f"{model[m][p]:<16}" for p in projects) for m in model))
    out = dict(groups=rows, explain=dict(name=ex.get("name"), member_count=ex.get("member_count"),
                                         permissions=[short_perm(p.get("permission")) for p in ex.get("permissions") or []]),
               mira=expl, model=model,
               members=[dict(slug=m["slug"], name=m["name"], title=m["title"], groups=m["groups"],
                             principal_id=sb["members"][m["slug"]]["id"], key=sb["keys"][m["slug"]]) for m in team["members"]])
    emit("groups", "", **out)
    return out


def explain_member(owner: CLI, sb: dict, slug: str, echo: bool = True) -> dict:
    """`member explain`: each project permission with the role / groups it comes from, and the projects reached."""
    ex = owner.run("member", "explain", sb["members"][slug]["id"], echo=echo) or {}
    gname = {v["id"]: k for k, v in (sb.get("groups") or {}).items()}

    def src(s: dict) -> str:
        return ("role" if s.get("type") == "role" else gname.get(s.get("id"), s.get("name") or s.get("id")))
    perms = [dict(permission=short_perm(p.get("permission")), sources=[src(s) for s in p.get("sources") or []])
             for p in ex.get("permissions") or [] if ":project:" in (p.get("permission") or "")]
    names = {v["id"]: k for k, v in sb["projects"].items()}
    pscope = next((s for s in ex.get("scopes") or [] if s.get("resource_type") == "memorylake:project"), {})
    reach = [dict(project=names.get(i.get("id"), i.get("id")), sources=[src(s) for s in i.get("sources") or []])
             for i in pscope.get("instances") or []]
    return dict(permissions=perms, reach=reach, restricted=pscope.get("restricted"))


# --------------------------------------------------------------------------- step 5: the second brain

def write_knowledge(you: CLI, sb: dict, team: dict) -> dict:
    sb["written"] = {}
    out = []
    for slug, by_project in team["knowledge"].items():
        c = member_cli(you, sb, slug)
        for p, texts in by_project.items():
            proj = sb["projects"][p]
            made = (c.run("fact", "add", "--project", proj["id"], *texts, scoped=True) or {}).get("facts") or []
            if len(made) != len(texts):
                die(f"fact add returned {len(made)} of {len(texts)} facts for {slug} in {p}")
            sb["written"].setdefault(slug, []).extend(f["id"] for f in made)
            out.append(dict(member=slug, project=p, facts=[dict(id=f["id"], text=f["fact"]) for f in made]))
            emit("text", f"\n  {team_name(team, slug)} → {proj['name']}: {len(made)} facts\n  " + "\n  ".join(
                f"  {clip(f['fact'], 104)}" for f in made))
    write_sandbox(sb)
    note("each researcher wrote with their own key; the server records no writer on a fact, so the demo keeps "
         "the ids each one wrote (.memorylake-demo/sandbox.json)")
    emit("knowledge", "", items=out)
    return dict(items=out)


def team_name(team: dict, slug: str) -> str:
    return next((m["name"] for m in team["members"] if m["slug"] == slug), slug)


# --------------------------------------------------------------------------- step 6: the matrix

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
        r["hits"] = hits
        r["detail"] = f"#1 {clip(hits[0].get('fact') or hits[0].get('content') or '', 60)}" if hits else (
            "0 hits" if r["rc"] == 0 else "")
    else:
        r = c.attempt("fact", "add", "--project", proj["id"], member["note"], scoped=True, echo=echo)
        made = ((r.get("body") or {}).get("facts")) or [] if r["rc"] == 0 else []
        r["fact_id"] = made[0]["id"] if made else None
        r["detail"] = f"added {r['fact_id']}" if made else ""
    return r


def matrix_step(you: CLI, sb: dict, team: dict) -> dict:
    note("every researcher now tries to read, search and write every project — with their own key")
    cells = []
    for m in team["members"]:
        c = member_cli(you, sb, m["slug"])
        for spec in team["projects"]:
            proj = sb["projects"][spec["slug"]]
            level = expected_level(team, m, spec["slug"])
            for op in OPS:
                r = try_op(c, op, proj, spec, m)
                expected = op in ALLOWED[level]
                allowed = r["rc"] == 0
                if allowed and op == "write" and r.get("fact_id"):
                    sb["written"].setdefault(m["slug"], []).append(r["fact_id"])
                cell = dict(member=m["slug"], project=spec["slug"], op=op, level=level, expected=expected,
                            allowed=allowed, status=r["status"], code=r["code"], message=r["message"],
                            detail=r["detail"], match=allowed == expected)
                cells.append(cell)
                emit("cell", "", **cell)
                if not cell["match"]:
                    note(f"MISMATCH: {m['slug']} {op} {spec['slug']} — the model says "
                         f"{'allow' if expected else 'deny'}, the server {'allowed it' if allowed else 'refused it'}")
    write_sandbox(sb)
    ok = sum(c["match"] for c in cells)
    lines = []
    for m in team["members"]:
        row = []
        for spec in team["projects"]:
            cs = [c for c in cells if c["member"] == m["slug"] and c["project"] == spec["slug"]]
            row.append(" ".join(f"{c['op'][0].upper()}{'✓' if c['allowed'] else '✗'}" + ("" if c["match"] else "!")
                                for c in cs))
        lines.append(f"{m['slug']:<8}" + "".join(f"{x:<16}" for x in row))
    emit("text", "\n  what the server allowed (R read · S search · W write; ✓ allowed, ✗ refused, ! differs from the model)\n  "
         + f"{'':<8}" + "".join(f"{p['slug']:<16}" for p in team["projects"]) + "\n  " + "\n  ".join(lines))
    refusals = sorted({(c["status"], c["code"]) for c in cells if not c["allowed"]})
    note(f"{ok}/{len(cells)} calls answered as the model says; refusals: "
         + ", ".join(f"HTTP {s} {code}" for s, code in refusals))
    for want in (("theo", "methods", "write"), ("theo", "funder-reports", "read")):
        c = next((x for x in cells if (x["member"], x["project"], x["op"]) == want and not x["allowed"]), None)
        if c:
            note(f"e.g. theo → {c['op']} {c['project']}: “{c['message']}”")
    out = dict(cells=cells, matched=ok, total=len(cells), refusals=[dict(status=s, code=c) for s, c in refusals])
    emit("matrix", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: the tidy-up that leaks

def leak_step(you: CLI, sb: dict, team: dict) -> dict:
    lk = team["leak"]
    owner = owner_cli(you, sb)
    c = member_cli(you, sb, lk["member"])
    m = next(x for x in team["members"] if x["slug"] == lk["member"])
    spec = next(p for p in team["projects"] if p["slug"] == lk["project"])
    proj = sb["projects"][lk["project"]]
    role = sb["role"]["key"]
    note(f"ticket {lk['ticket']}")
    note("same projects, no per-project action lists: the help says `actions` is optional")
    owner.run("role", "scope", "set", role, "--reason", lk["ticket"], "--scopes", role_scope(sb, team, with_actions=False))
    w1 = try_op(c, "write", proj, spec, m)
    leaked = w1["rc"] == 0
    note(f"{m['name']} writes to {proj['name']}: " + (f"rc 0 — {w1['detail']}" if leaked else
                                                       f"HTTP {w1['status']} “{w1['message']}”"))
    ex1 = explain_member(owner, sb, lk["member"])
    add1 = next((p for p in ex1["permissions"] if p["permission"] == "project:mem_add"), None)
    reach1 = next((r for r in ex1["reach"] if r["project"] == lk["project"]), None)
    if leaked:
        note(f"member explain: project:mem_add comes from {', '.join(add1['sources']) if add1 else '—'}; "
             f"{lk['project']} is reached through {', '.join(reach1['sources']) if reach1 else '—'}")
        note("the field crew's write was meant for the field notes: without action lists on the role's projects, "
             "it lands on every project the role reaches")
    else:
        note("the server kept the methods read-only this time — the tidy-up did not leak")
    note("put the action lists back")
    owner.run("role", "scope", "set", role, "--reason", lk["ticket"] + " (reverted)", "--scopes", role_scope(sb, team))
    w2 = try_op(c, "write", proj, spec, m)
    fn = next(p for p in team["projects"] if p["slug"] == "field-notes")
    w3 = try_op(c, "write", sb["projects"]["field-notes"], fn, m, echo=False)
    note(f"{m['name']} writes to {proj['name']} now: " + (f"HTTP {w2['status']} “{w2['message']}”" if w2["rc"] else
                                                           "rc 0 (still allowed!)")
         + f" · field notes: {'rc 0' if w3['rc'] == 0 else 'HTTP ' + str(w3['status'])}")
    for f in (w1.get("fact_id"), w2.get("fact_id")):
        if f:
            owner.run("fact", "delete", "--project", proj["id"], f, scoped=True)
            note(f"the note that should never have reached {proj['name']} is deleted ({f})")
    if w3.get("fact_id"):
        sb["written"].setdefault(lk["member"], []).append(w3["fact_id"])
        write_sandbox(sb)
    ok = w2["rc"] != 0 and w3["rc"] == 0
    out = dict(member=lk["member"], project=lk["project"], ticket=lk["ticket"], leaked=leaked,
               leak=dict(rc=w1["rc"], status=w1["status"], message=w1["message"], detail=w1["detail"]),
               mem_add_sources=add1["sources"] if add1 else [], reach_sources=reach1["sources"] if reach1 else [],
               fixed=dict(rc=w2["rc"], status=w2["status"], message=w2["message"]),
               field_notes_rc=w3["rc"], ok=ok)
    emit("leak", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: Mira leaves

def answer(c: CLI, sb: dict, team: dict, project: str, q: dict, echo: bool = True) -> dict:
    proj = sb["projects"][project]
    r = c.attempt("search", q["q"], "--projects", proj["id"], "--top-k", "3", scoped=True, echo=echo)
    hits = ((r.get("body") or {}).get("facts")) or [] if r["rc"] == 0 else []
    top = hits[0] if hits else {}
    writer = next((s for s, ids in (sb.get("written") or {}).items() if top.get("id") in ids), None)
    text = top.get("fact") or top.get("content") or ""
    return dict(q=q["q"], rc=r["rc"], status=r["status"], message=r["message"], id=top.get("id"), text=text,
                writer=writer, found=q["expect"].lower() in text.lower())


def departure_step(you: CLI, sb: dict, team: dict) -> dict:
    d = team["departure"]
    owner = owner_cli(you, sb)
    leaver = member_cli(you, sb, d["member"])
    reader = member_cli(you, sb, d["reader"])
    proj = sb["projects"][d["project"]]
    pid = sb["members"][d["member"]]["id"]
    mine = set(sb["written"].get(d["member"]) or [])
    note(f"ticket {d['ticket']}")
    before = leaver.attempt("fact", "list", "--projects", proj["id"], "--page-size", "50", scoped=True)
    n_before = len(((before.get("body") or {}).get("items")) or []) if before["rc"] == 0 else None
    note(f"{team_name(team, d['member'])} reads {proj['name']}: "
         + (f"rc 0, {n_before} facts" if before["rc"] == 0 else before["message"]))
    owner.run("member", "remove", pid)
    t0, first = time.time(), True
    while True:
        r = leaver.attempt("fact", "list", "--projects", proj["id"], "--page-size", "50", scoped=True, echo=first)
        first = False
        after_s = round(time.time() - t0, 1)
        if r["rc"] != 0 or after_s > REFUSAL_WAIT:
            break
        time.sleep(1)
    note(f"the same call {after_s}s later: " + (f"HTTP {r['status']} “{r['message']}”" if r["rc"] else "STILL ACCEPTED"))
    groups_after = {}
    for g in team["groups"]:
        if d["member"] in [m["slug"] for m in team["members"] if g["slug"] in m["groups"]]:
            items = (owner.run("access-group", "member", "list", sb["groups"][g["slug"]]["id"]) or {}).get("items") or []
            ids = {x.get("principal_id") or x.get("id") for x in items}
            groups_after[g["slug"]] = dict(members=len(items), still_listed=pid in ids)
    listed = [g for g, v in groups_after.items() if v["still_listed"]]
    note("her groups after `member remove`: " + ", ".join(f"{g} {v['members']} member(s)" for g, v in groups_after.items())
         + (f" — still listed in {', '.join(listed)}" if listed else " — she is in none of them any more"))
    facts = _pages(owner, ["fact", "list", "--projects", proj["id"]], echo=False)
    kept = [f for f in facts if f.get("id") in mine]
    note(f"{proj['name']} still holds {len(facts)} facts, {len(kept)} of them written by "
         f"{team_name(team, d['member'])}")
    answers = [answer(reader, sb, team, d["project"], q) for q in d["questions"]]
    emit("text", f"\n  {team_name(team, d['reader'])} asks the second brain (search {proj['name']}, rank 1)\n  " + "\n  ".join(
        f"  {'✓' if a['found'] else '✗'} {a['q']}\n      → {clip(a['text'], 100)}"
        + (f"\n        written by {team_name(team, a['writer'])}" if a["writer"] else "") for a in answers))
    by_leaver = sum(1 for a in answers if a["writer"] == d["member"])
    note(f"{sum(a['found'] for a in answers)}/{len(answers)} answered from memory; {by_leaver} of the answers were "
         f"written by {team_name(team, d['member'])}, whose key no longer works")
    shutil.rmtree(MEMBERS_DIR / d["member"], ignore_errors=True)
    sb["members"][d["member"]]["removed"] = True
    write_sandbox(sb)
    ok = before["rc"] == 0 and r["rc"] != 0 and len(kept) == len(mine & {f.get("id") for f in facts}) and \
        all(a["found"] for a in answers)
    out = dict(member=d["member"], ticket=d["ticket"], before=n_before,
               refused=dict(rc=r["rc"], status=r["status"], code=r["code"], message=r["message"], after_s=after_s),
               groups_after=groups_after, kept=len(kept), facts=len(facts), answers=answers, by_leaver=by_leaver, ok=ok)
    emit("departure", "", **out)
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


def audit_step(you: CLI, sb: dict, team: dict, echo: bool = True) -> dict:
    owner = owner_cli(you, sb)
    rows = audit_rows(owner, sb.get("created_at"), echo=echo)
    gname = {v["id"]: v["name"] for v in (sb.get("groups") or {}).values()}
    for r in rows:
        r["target"] = gname.get(r["target"], r["target"])
    emit("text", "\n  " + f"{'time (UTC)':<21}{'action':<30}{'by':<12}target\n  " + "\n  ".join(
        f"{iso(r['at']):<21}{r['action'] or '':<30}{(r['by'] or '')[:11]:<12}{r['target'] or ''}" for r in rows))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    note(f"{len(rows)} event(s) since the sandbox was created: " + ", ".join(f"{k} ×{v}" for k, v in counts.items()))
    note("the custom role is `custom_role:create`, each permission rule a `policy:create` (one per action), "
         "and a role scope change is logged as `member:set_scope` with the role as its target")
    gm = [r for r in rows if r["action"] in ("access_group:member_add", "access_group:member_remove")]
    if gm:
        note(f"group membership events name the group, not who joined it ({len(gm)} event(s))")
    out = dict(rows=[{k: v for k, v in r.items() if k != "fields"} for r in rows], counts=counts)
    emit("audit", "", **out)
    return out


# --------------------------------------------------------------------------- report

def write_report(team: dict, sb: dict, res: dict) -> dict:
    doc = dict(company=team["company"]["name"], generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               sandbox=dict(team_id=sb["team_id"], team_name=sb["team_name"], workspace=sb.get("workspace")),
               role=res["role"], groups=res["groups"], matrix=res["matrix"], leak=res["leak"],
               departure=res["departure"], audit=res["audit"])
    OUT.mkdir(exist_ok=True)
    (OUT / "second-brain-review.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    m, lk, dp = res["matrix"], res["leak"], res["departure"]
    lines = [f"# {team['company']['name']} — second brain access review", "",
             f"Generated {doc['generated_at']} against a sandbox org team ({sb['team_id']}). Every cell is a real call "
             "made with that researcher's own API key; the server's answer is recorded as given.", "",
             f"Role `{res['role']['key']}` “{res['role']['label']}”: {', '.join(res['role']['permissions'])}.", "",
             f"**{m['matched']}/{m['total']} calls answered as the access model says.**", "",
             "| researcher | project | model | read | search | write |", "|---|---|---|---|---|---|"]
    for mem in team["members"]:
        for p in team["projects"]:
            cs = {c["op"]: c for c in m["cells"] if c["member"] == mem["slug"] and c["project"] == p["slug"]}
            lvl = next(iter(cs.values()))["level"]
            fmt = lambda c: ("✓" if c["allowed"] else f"✗ {c['status']}") + ("" if c["match"] else " (differs)")
            lines.append(f"| {mem['slug']} | {p['slug']} | {lvl} | {fmt(cs['read'])} | {fmt(cs['search'])} | {fmt(cs['write'])} |")
    lines += ["", "## Role scope without action lists", "",
              f"{lk['ticket']}: {lk['member']} → write {lk['project']}: "
              + (f"**rc 0** (mem_add from {', '.join(lk['mem_add_sources'])})" if lk["leaked"] else f"HTTP {lk['leak']['status']}")
              + f" · with the action lists back: HTTP {lk['fixed']['status'] or 'rc 0'}", "",
              "## Departure", "",
              f"{dp['ticket']}: key refused {dp['refused']['after_s']} s after `member remove` — HTTP "
              f"{dp['refused']['status']} “{dp['refused']['message']}”; {dp['kept']} fact(s) she wrote stay.", ""]
    for a in dp["answers"]:
        lines.append(f"- {a['q']} → {a['text']}" + (f" (written by {a['writer']})" if a["writer"] else ""))
    lines += ["", "## Audit log", "", "| time (UTC) | action | by | target |", "|---|---|---|---|"]
    for r in res["audit"]["rows"]:
        lines.append(f"| {iso(r['at'])} | {r['action']} | {r['by']} | {r['target']} |")
    (OUT / "second-brain-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return doc


def show_report() -> dict:
    p = OUT / "second-brain-review.json"
    if not p.exists():
        die("no review yet; run `python3 demo.py` first")
    doc = json.loads(p.read_text(encoding="utf-8"))
    m = doc["matrix"]
    emit("text", f"\n  {doc['company']} — second brain access review ({doc['generated_at']})\n"
                 f"  {m['matched']}/{m['total']} calls answered as the access model says\n"
                 f"  full report: out/second-brain-review.md")
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
            note(f"deleted the sandbox team {sb['team_id']} — its projects, role, groups, members and keys go with it")
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
    note("deleted the sandbox and researcher profiles under .memorylake-demo/ (your own login stays)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(you: CLI, reset: bool = False) -> dict:
    team = load_team()
    t0 = time.time()
    emit("intro", "", company=team["company"], members=team["members"],
         projects=[dict(slug=p["slug"], name=p["name"], description=p["description"]) for p in team["projects"]],
         role=team["role"], groups=team["groups"])
    banner(2, TOTAL, "A sandbox org team — the only kind of team that can have members")
    if reset or read_sandbox():
        note("--reset" if reset else "found a sandbox team from an earlier run")
        note("teams, roles, groups and keys are one-shot, so every run starts from nothing: deleting it first")
        cleanup(you, quiet=True)
    sb = create_sandbox(you, team)
    banner(3, TOTAL, "A custom role from permission rules: “Lab researcher” reads two projects")
    role = define_role(you, sb, team)
    banner(4, TOTAL, "Access groups add the rest: field crew, method stewards, grant team")
    groups = define_groups(you, sb, team)
    banner(5, TOTAL, "The second brain fills up — each researcher writes with their own key")
    write_knowledge(you, sb, team)
    banner(6, TOTAL, "27 calls: every researcher × every project × read / search / write")
    mx = matrix_step(you, sb, team)
    banner(7, TOTAL, "A tidy-up of the role scope lets a group's write land on the methods")
    lk = leak_step(you, sb, team)
    banner(8, TOTAL, "Mira leaves: her key stops, her knowledge stays")
    dp = departure_step(you, sb, team)
    banner(9, TOTAL, "The audit log: role, group and member changes")
    au = audit_step(you, sb, team)
    write_report(team, sb, dict(role=role, groups=groups, matrix=mx, leak=lk, departure=dp, audit=au))
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   matched=mx["matched"], total=mx["total"], refusals=mx["refusals"], leaked=lk["leaked"],
                   leak_sources=lk["mem_add_sources"], leak_fixed=lk["ok"], departure_ok=dp["ok"],
                   refused_s=dp["refused"]["after_s"], refused_status=dp["refused"]["status"], kept=dp["kept"],
                   answers_found=sum(a["found"] for a in dp["answers"]), answers_by_leaver=dp["by_leaver"],
                   groups_after=dp["groups_after"], audit_events=len(au["rows"]), audit_counts=au["counts"])
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    emit("summary", "", **summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "report", "audit", "cleanup"],
                    help="run = full demo (default); report = print the last review; audit = read the sandbox "
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
        audit_step(you, sb, load_team())
        return
    run_pipeline(you, reset=args.reset)
    print("\nDone. The sandbox team is still there so you can look around: `python3 demo.py audit` reads its audit "
          "log again,\n`python3 demo.py report` prints the review (out/second-brain-review.md), "
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

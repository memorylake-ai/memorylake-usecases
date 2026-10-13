#!/usr/bin/env python3
"""
Shared memory for agents on different networks: every agent's key pinned to
its network, reversible suspension, and an audit trail — a MemoryLake demo
driven entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harborline Freight (a fictional freight forwarder) runs a dispatch agent.
Kestrel Customs Brokerage (a fictional customs broker) runs a broker agent on
its own network. The two agents share one memory — the shipment contract —
and Harborline keeps a second memory to itself. The contract is written down
in data/contract.json: which agent reaches which memory, from which network.

The demo builds that for real and then exercises it:

- a sandbox org team is created on your account (`team create
  --issue-api-key`); each agent is a virtual member with its own key, limited
  to the memory the contract names (`member scope set`)
- Kestrel's key is pinned to Kestrel's network (`api-key create
  --allowed-ip 198.51.100.0/24`). Used from this machine it is refused before it
  touches memory — and the refusal names this machine's IP, which the demo
  then pins Harborline's key to
- dispatch writes the shipments; once the contract adds this machine to
  Kestrel's allowlist (`api-key update --allowed-ip`), the broker reads them,
  records a customs hold, and is refused on Harborline's internal memory
- Kestrel reports its key may have leaked: `api-key update --status disabled`
  refuses it on the next call; what it wrote stays; `--status enabled` brings
  back the same key — nothing to redistribute
- Kestrel moves back behind its own egress: the allowlist drops this machine,
  and the next call from here is refused with this machine's IP
- `audit-log list` shows every contract change and who made it; refused calls
  are not in it, so the demo keeps its own access ledger of every agent call
  (out/access-ledger.jsonl)

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
MEMBERS_DIR = STATE_DIR / "members"    # one profile per agent key
SANDBOX_FILE = STATE_DIR / "sandbox.json"  # the sandbox team's id and objects (no secrets)
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-xnm"  # memorylake-usecases / cross-network-memory

REFUSAL_WAIT = 30  # seconds a suspended key is polled until the server refuses it

SECRET_RE = re.compile(r"sk-([A-Za-z0-9]{4})[A-Za-z0-9_-]{8,}")


# For screen recordings: MLU_MASK_IP=1 cuts every public IPv4 address but the documentation range to its first two
# octets (103.151.•.•), on screen and in the web app's events. The server still sees, and the keys still pin, the real one.
MASK_IP = os.environ.get("MLU_MASK_IP") == "1"
IPV4_RE = re.compile(r"\b(?!198\.51\.100\.)(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")


def hide_keys(s: str) -> str:
    return SECRET_RE.sub(lambda m: f"sk-{m.group(1)}…", s or "")


def mask(s: str) -> str:
    """What reaches the screen: every API key cut to sk- + 4 characters (and, for recordings, public IPs)."""
    s = hide_keys(s)
    return IPV4_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}.•.•", s) if MASK_IP else s


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
    if MASK_IP and data:
        data = json.loads(mask(json.dumps(data, ensure_ascii=False)))
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
    sandbox team) or one of the agents, each in its own isolated profile under .memorylake-demo/."""

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
    lines = [ln.strip() for ln in hide_keys(text).splitlines() if ln.strip()]  # keys only: the IP is parsed later
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
    if m:
        return m.group(1)
    m = re.search(r"\(([a-z]+_error)\)", text or "")  # a refusal at the gateway: (permission_error), (authentication_error)
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

def load_contract() -> dict:
    return json.loads((DATA / "contract.json").read_text(encoding="utf-8"))


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

def create_sandbox(you: CLI, contract: dict) -> dict:
    if (you.team or {}).get("type") != "personal":  # type: ignore[attr-defined]
        die("run this demo with an API key of your *personal* team: it creates a separate sandbox org team for "
            "the agents and deletes it at the end, so nothing is added to a real org team")
    note("a personal team cannot have members; an org team can. The demo creates one just for this run")
    note("an account may own one org team besides its personal team: if yours already has one, this step stops")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    r = you.attempt("team", "create", "--name", contract["team_name"], "--issue-api-key",
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


# --------------------------------------------------------------------------- the access ledger

# The audit log records who changed the contract, not the calls the server refused. Every call an agent makes
# in this demo is written here too — who, what, which memory, the server's answer, and the IP it reported.
LEDGER: list[dict] = []

IP_RE = re.compile(r"\bIP ([0-9A-Fa-f:.]+) is not allowed")


def reported_ip(r: dict) -> str:
    m = IP_RE.search(r.get("message") or "")
    return m.group(1) if m else ""


def why_refused(r: dict) -> str:
    if r["rc"] == 0:
        return ""
    if reported_ip(r):
        return "network"
    if r["status"] == 401:
        return "key"
    if r["status"] == 403:
        return "scope"
    return "other"


def agent_call(c: CLI, slug: str, op: str, project: dict | None, *args: str, echo: bool = True) -> dict:
    r = c.attempt(*args, scoped=project is not None, echo=echo)
    row = dict(at=round(time.time(), 1), agent=slug, op=op, project=(project or {}).get("name"),
               allowed=r["rc"] == 0, status=r["status"], code=r["code"], why=why_refused(r),
               ip=reported_ip(r), message=r["message"])
    LEDGER.append(row)
    emit("call", "", **row)
    return r


def answer_line(r: dict) -> str:
    if r["rc"] == 0:
        return "rc 0"
    return f"HTTP {r['status']} " + (f"{r['code']} " if r["code"] else "") + f"“{r['message']}”"


# --------------------------------------------------------------------------- step 3: two memories, two agents

# What each level may do on a project. A scope instance without `actions` gets every action the role allows,
# so each instance carries its actions.
ACTIONS = {
    "read": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search",
             "project:search"],
}
ACTIONS["write"] = ACTIONS["read"] + ["project:mem_add", "project:doc_add", "project:mem_conflict_read"]


def scope_entries(sb: dict, grants: dict) -> str:
    inst = [dict(id=sb["projects"][p]["id"], workspace_id=sb["workspace"], actions=ACTIONS[level])
            for p, level in grants.items()]
    return json.dumps([dict(resource_type="memorylake:project", mode="instances", instances=inst)])


def setup_step(you: CLI, sb: dict, contract: dict) -> dict:
    owner = owner_cli(you, sb)
    for p in contract["projects"]:
        proj = owner.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["slug"]),
                         "--description", p["description"], scoped=True)
        sb["projects"][p["slug"]] = dict(id=proj["id"], name=p["name"], shared=p["shared"])
        note(f"{p['name']} ({proj['id']}) — {'shared with the partner' if p['shared'] else 'Harborline only'}")
    write_sandbox(sb)
    rows = []
    for a in contract["agents"]:
        made = owner.run("member", "create", "--name", a["name"][:20], "--role", "tenant_member")
        pid = made["principal_id"]
        sb["members"][a["slug"]] = dict(id=pid, name=a["name"])
        owner.run("member", "scope", "set", pid, "--reason", f"contract: {a['org']} — {a['purpose']}",
                  "--scopes", scope_entries(sb, a["grants"]))
        rows.append(dict(agent=a["slug"], org=a["org"], principal_id=pid, grants=a["grants"], network=a["network"]))
        note(f"{a['name']} ({a['org']}): virtual member {pid}, reaches "
             + ", ".join(f"{sb['projects'][p]['name']} ({lvl})" for p, lvl in a["grants"].items()))
    write_sandbox(sb)
    emit("setup", "", projects=[dict(slug=k, **v) for k, v in sb["projects"].items()], agents=rows)
    return dict(agents=rows)


# --------------------------------------------------------------------------- step 4: keys pinned to networks

_PENDING: dict[str, str] = {}  # a key that cannot log in yet (its network excludes this machine); memory only


def issue_key(owner: CLI, sb: dict, slug: str, allowed: list[str]) -> str:
    k = owner.run("api-key", "create", "--name", cid(slug), "--member", sb["members"][slug]["id"],
                  "--allowed-ip", ",".join(allowed))
    secret = k.get("key") or ""
    if not secret:
        die(f"`api-key create --member` returned no key for {slug}")
    sb["keys"][slug] = dict(id=str(k["id"]), name=cid(slug), prefix=k.get("key_prefix"), allowed=allowed)
    write_sandbox(sb)
    note(f"{slug}: key {cid(slug)} (id {k['id']}, {mask(secret)}) — allowed from {', '.join(allowed)}")
    return secret


def try_login(you: CLI, sb: dict, slug: str, secret: str) -> dict:
    """`auth login` checks the key against the server, so a key used outside its network fails right here."""
    d = MEMBERS_DIR / slug
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    c = profile_cli(you, d, slug, sb["workspace"])
    r = c.attempt("auth", "login", "--api-key", secret, "--base-url", you.base_url, "--profile", PROFILE)
    LEDGER.append(dict(at=round(time.time(), 1), agent=slug, op="login", project=None, allowed=r["rc"] == 0,
                       status=r["status"], code=r["code"], why=why_refused(r), ip=reported_ip(r), message=r["message"]))
    emit("call", "", **LEDGER[-1])
    if r["rc"] != 0:
        shutil.rmtree(d, ignore_errors=True)
    return r


def keys_step(you: CLI, sb: dict, contract: dict) -> dict:
    owner = owner_cli(you, sb)
    net = contract["partner_network"]
    note(f"Kestrel's agent calls from {net['cidr']} — its key is pinned there")
    note(net["why"])
    secret = issue_key(owner, sb, "kestrel-broker", [net["cidr"]])
    r = try_login(you, sb, "kestrel-broker", secret)
    ip = reported_ip(r)
    if r["rc"] == 0:
        die(f"the key pinned to {net['cidr']} was accepted from this machine — this machine is inside that range, "
            "or the server ignored the restriction. Nothing else in the demo would mean anything; stopping here")
    if not ip:
        die(f"the pinned key was refused, but not for its network: HTTP {r['status']} {r['message']}")
    _PENDING["kestrel-broker"] = secret
    del secret
    note(f"refused before it touched memory: {answer_line(r)}")
    note(f"the server just told us this machine's IP: {ip}. Harborline's dispatch agent runs here, so its key is "
         "pinned to exactly that address")
    sb["egress"] = ip
    secret = issue_key(owner, sb, "harborline-dispatch", [ip])
    r2 = try_login(you, sb, "harborline-dispatch", secret)
    del secret
    if r2["rc"] != 0:
        die(f"harborline-dispatch could not log in from {ip}: HTTP {r2['status']} {r2['message']}")
    note(f"harborline-dispatch logged in from {ip} — its key lives in .memorylake-demo/members/harborline-dispatch/")
    out = dict(partner=dict(cidr=net["cidr"], status=r["status"], code=r["code"], message=r["message"]), egress=ip,
               keys={s: dict(name=v["name"], id=v["id"], prefix=v["prefix"], allowed=v["allowed"])
                     for s, v in sb["keys"].items()})
    emit("keys", "", **out)
    return out


def check_egress(r: dict, sb: dict) -> None:
    ip = reported_ip(r)
    if ip and sb.get("egress") and ip != sb["egress"]:
        die(f"this machine's IP changed during the run ({sb['egress']} → {ip}; a VPN or a network switch?). "
            "Keys are pinned to the address the server saw at step 4 — run the demo again")


# --------------------------------------------------------------------------- step 5: dispatch writes

def stamp(c: CLI, project: dict, fact_ids: list[str], slug: str) -> None:
    # the server records no writer on a fact: each agent stamps its own name (self-reported)
    for fid in fact_ids:
        c.attempt("fact", "update", fid, "--project", project["id"], "--metadata",
                  json.dumps({"written_by": slug}), scoped=True, echo=False)


def dispatch_step(you: CLI, sb: dict, contract: dict) -> dict:
    c = member_cli(you, sb, "harborline-dispatch")
    out = {}
    for slug in ("contract", "internal"):
        proj = sb["projects"][slug]
        texts = contract["writes"][slug]
        r = agent_call(c, "harborline-dispatch", "write", proj, "fact", "add", "--project", proj["id"], *texts)
        check_egress(r, sb)
        if r["rc"] != 0:
            die(f"harborline-dispatch could not write to {proj['name']}: {answer_line(r)}")
        facts = ((r.get("body") or {}).get("facts")) or []
        ids = [f["id"] for f in facts]
        stamp(c, proj, ids, "harborline-dispatch")
        sb.setdefault("written", {}).update({fid: "harborline-dispatch" for fid in ids})
        out[slug] = [f["fact"] for f in facts]
        emit("text", f"\n  {proj['name']} — {len(facts)} facts by harborline-dispatch\n  " + "\n  ".join(
            f"  {clip(f['fact'], 104)}" for f in facts))
    write_sandbox(sb)
    emit("dispatch", "", written=out)
    return out


# --------------------------------------------------------------------------- step 6: the partner joins

def hits_of(r: dict) -> list[dict]:
    body = r.get("body") or {}
    return (body.get("facts") or []) if isinstance(body, dict) else []


def hit_text(h: dict) -> str:
    return h.get("content") or h.get("fact") or h.get("text") or ""


def writer_of(h: dict, sb: dict) -> str:
    return ((h.get("metadata") or {}).get("written_by")) or (sb.get("written") or {}).get(h.get("id"), "?")


def partner_step(you: CLI, sb: dict, contract: dict) -> dict:
    owner = owner_cli(you, sb)
    net, ip = contract["partner_network"], sb["egress"]
    key = sb["keys"]["kestrel-broker"]
    note(f"for this demo, this machine also plays Kestrel's network: the contract adds {ip} to the broker's allowlist")
    allowed = [net["cidr"], ip]
    owner.run("api-key", "update", key["id"], "--allowed-ip", ",".join(allowed))
    key["allowed"] = allowed
    write_sandbox(sb)
    secret = _PENDING.pop("kestrel-broker", "")
    if not secret:
        die("the broker's key is gone from memory (it is never written to disk before it can log in) — run the demo "
            "again")
    r = try_login(you, sb, "kestrel-broker", secret)
    del secret
    if r["rc"] != 0:
        die(f"kestrel-broker still cannot log in: {answer_line(r)}")
    note("the same key that was refused at step 4 now logs in — nothing about the key changed, only the contract")
    b = member_cli(you, sb, "kestrel-broker")
    d = member_cli(you, sb, "harborline-dispatch")
    con, internal = sb["projects"]["contract"], sb["projects"]["internal"]
    q = contract["questions"]
    cells = []

    def cell(agent: str, op: str, proj: dict, expected: bool, r: dict, detail: str = "") -> dict:
        x = dict(agent=agent, op=op, project=proj["name"], expected=expected, allowed=r["rc"] == 0,
                 status=r["status"], code=r["code"], message=r["message"], detail=detail)
        x["match"] = x["allowed"] == expected
        cells.append(x)
        return x

    r1 = agent_call(b, "kestrel-broker", "search", con, "search", q["partner"]["query"], "--projects", con["id"],
                    "--types", "fact", "--top-k", "4")
    hits = hits_of(r1)
    cell("kestrel-broker", "search", con, True, r1, f"{len(hits)} hits")
    if hits:
        emit("text", "\n  kestrel-broker asks: " + q["partner"]["query"] + "\n  " + "\n  ".join(
            f"  #{i} {clip(hit_text(h), 96)}  [{writer_of(h, sb)}]" for i, h in enumerate(hits, 1)))
    texts = contract["writes"]["partner"]
    r2 = agent_call(b, "kestrel-broker", "write", con, "fact", "add", "--project", con["id"], *texts)
    made = ((r2.get("body") or {}).get("facts")) or []
    cell("kestrel-broker", "write", con, True, r2, f"added {len(made)}")
    stamp(b, con, [f["id"] for f in made], "kestrel-broker")
    sb.setdefault("written", {}).update({f["id"]: "kestrel-broker" for f in made})
    write_sandbox(sb)
    if made:
        emit("text", f"\n  {con['name']} — {len(made)} facts by kestrel-broker\n  " + "\n  ".join(
            f"  {clip(f['fact'], 104)}" for f in made))
    r3 = agent_call(b, "kestrel-broker", "read", internal, "fact", "list", "--projects", internal["id"],
                    "--page-size", "20")
    cell("kestrel-broker", "read", internal, False, r3)
    r4 = agent_call(b, "kestrel-broker", "search", internal, "search", "margin on HX-4471", "--projects",
                    internal["id"], "--types", "fact", "--top-k", "3")
    cell("kestrel-broker", "search", internal, False, r4)
    note(f"kestrel-broker on {internal['name']}: {answer_line(r3)}")
    # dispatch asks the shared memory a question only the partner's agent could answer
    r5 = agent_call(d, "harborline-dispatch", "search", con, "search", q["dispatch"]["query"], "--projects",
                    con["id"], "--types", "fact", "--top-k", "3")
    check_egress(r5, sb)
    dh = hits_of(r5)
    rank = next((i for i, h in enumerate(dh, 1) if writer_of(h, sb) == q["dispatch"]["expect_writer"]
                 and q["dispatch"]["expect_text"] in hit_text(h).lower()), None)
    cell("harborline-dispatch", "search", con, True, r5, f"partner's answer at #{rank}" if rank else "partner's answer not in top 3")
    if dh:
        emit("text", "\n  harborline-dispatch asks: " + q["dispatch"]["query"] + "\n  " + "\n  ".join(
            f"  #{i} {clip(hit_text(h), 96)}  [{writer_of(h, sb)}]" for i, h in enumerate(dh, 1)))
    note(f"Kestrel's hold, written from the partner's side, is #{rank} in Harborline's answer" if rank else
         "Kestrel's hold is not in the top 3 of Harborline's answer this time (ranking varies; it is in the memory)")
    ok = sum(x["match"] for x in cells)
    emit("text", "\n  contract check (every call made with the agent's own key)\n  " + "\n  ".join(
        f"  {x['agent']:<20} {x['op']:<7} {x['project']:<20} {'✓ allowed' if x['allowed'] else '✗ ' + str(x['status']) + ' ' + x['code']:<22}"
        f"{'' if x['match'] else ' (differs from the contract!)'} {x['detail']}" for x in cells))
    note(f"{ok}/{len(cells)} calls answered as the contract says")
    ans = lambda hs: [dict(text=hit_text(h), writer=writer_of(h, sb)) for h in hs]
    out = dict(allowed=allowed, cells=cells, matched=ok, total=len(cells), rank=rank,
               questions=dict(partner=q["partner"]["query"], dispatch=q["dispatch"]["query"]),
               partner_hits=ans(hits), dispatch_hits=ans(dh), partner_wrote=[f["fact"] for f in made],
               refusal=dict(status=r3["status"], code=r3["code"], message=r3["message"]))
    emit("partner", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: suspend and resume

def wait_until(c: CLI, slug: str, proj: dict, refused: bool) -> tuple[dict, float]:
    t0, first = time.time(), True
    while True:
        r = agent_call(c, slug, "read", proj, "fact", "list", "--projects", proj["id"], "--page-size", "20",
                       echo=first)
        first = False
        s = round(time.time() - t0, 1)
        if (r["rc"] != 0) == refused or s > REFUSAL_WAIT:
            return r, s
        time.sleep(1)


def suspend_step(you: CLI, sb: dict, contract: dict) -> dict:
    inc = contract["incident"]
    owner = owner_cli(you, sb)
    b = member_cli(you, sb, "kestrel-broker")
    d = member_cli(you, sb, "harborline-dispatch")
    con = sb["projects"]["contract"]
    key = sb["keys"]["kestrel-broker"]
    note(f"ticket {inc['reason']}")
    owner.run("api-key", "update", key["id"], "--status", "disabled")
    off, off_s = wait_until(b, "kestrel-broker", con, refused=True)
    note(f"kestrel-broker, same key, {off_s}s later: {answer_line(off)}")
    mine = [fid for fid, w in (sb.get("written") or {}).items() if w == "kestrel-broker"]
    listed = _pages(d, ["fact", "list", "--projects", con["id"]], echo=True)
    kept = [f for f in listed if f.get("id") in mine]
    note(f"what kestrel-broker wrote stays in the contract: {len(kept)}/{len(mine)} of its facts, read by "
         "harborline-dispatch while the key is suspended")
    k1 = owner.run("api-key", "get", key["id"]) or {}
    note(f"api-key get {key['id']}: status “{k1.get('status')}”, allowed_ips {k1.get('allowed_ips')}")
    note(f"ticket {inc['resolved']}")
    owner.run("api-key", "update", key["id"], "--status", "enabled")
    on, on_s = wait_until(b, "kestrel-broker", con, refused=False)
    n_on = len(((on.get("body") or {}).get("items")) or []) if on["rc"] == 0 else None
    note(f"the same key, never re-issued, {on_s}s later: " + (f"rc 0, {n_on} facts" if on["rc"] == 0 else answer_line(on)))
    ok = off["rc"] != 0 and off["status"] == 401 and on["rc"] == 0 and len(kept) == len(mine)
    out = dict(ticket=inc["ticket"], reason=inc["reason"], resolved=inc["resolved"],
               off=dict(status=off["status"], code=off["code"], message=off["message"], after_s=off_s),
               kept=len(kept), wrote=len(mine), on=dict(rc=on["rc"], facts=n_on, after_s=on_s),
               key_status=k1.get("status"), ok=ok)
    emit("suspend", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: the partner's network changes

def egress_step(you: CLI, sb: dict, contract: dict) -> dict:
    ch, net = contract["egress_change"], contract["partner_network"]
    owner = owner_cli(you, sb)
    b = member_cli(you, sb, "kestrel-broker")
    d = member_cli(you, sb, "harborline-dispatch")
    con = sb["projects"]["contract"]
    key = sb["keys"]["kestrel-broker"]
    note(f"ticket {ch['reason']}")
    owner.run("api-key", "update", key["id"], "--allowed-ip", net["cidr"])
    key["allowed"] = [net["cidr"]]
    write_sandbox(sb)
    t0 = time.time()
    r = agent_call(b, "kestrel-broker", "read", con, "fact", "list", "--projects", con["id"], "--page-size", "20")
    took = round(time.time() - t0, 1)
    note(f"kestrel-broker from {sb['egress']}: {answer_line(r)}")
    r2 = agent_call(d, "harborline-dispatch", "read", con, "fact", "list", "--projects", con["id"], "--page-size", "20")
    check_egress(r2, sb)
    note(f"harborline-dispatch from the same machine, its own key: {answer_line(r2)}"
         + (f", {len(((r2.get('body') or {}).get('items')) or [])} facts" if r2["rc"] == 0 else ""))
    keys = (owner.run("api-key", "list") or {}).get("items") or []
    rows = [dict(name=k.get("name"), id=str(k.get("id")), owner=(k.get("owner") or {}).get("display_name"),
                 allowed=k.get("allowed_ips") or [], status=k.get("status")) for k in keys]
    emit("text", "\n  the contract as the server holds it (api-key list)\n  "
         + f"{'key':<30}{'agent':<22}{'status':<10}allowed from\n  " + "\n  ".join(
             f"{r_['name']:<30}{(r_['owner'] or '')[:21]:<22}{r_['status'] or '':<10}{', '.join(r_['allowed']) or 'anywhere'}"
             for r_ in rows))
    ok = r["rc"] != 0 and reported_ip(r) == sb["egress"] and r2["rc"] == 0
    out = dict(ticket=ch["ticket"], reason=ch["reason"], partner=dict(status=r["status"], code=r["code"],
               message=r["message"], ip=reported_ip(r), seconds=took), dispatch_rc=r2["rc"], keys=rows, ok=ok)
    emit("egress", "", **out)
    return out


# --------------------------------------------------------------------------- step 9: audit log + access ledger

def audit_rows(owner: CLI, since: int | None, echo: bool = True) -> list[dict]:
    items = (owner.run("audit-log", "list", "--page-size", "100", echo=echo) or {}).get("items") or []
    rows = []
    for e in items:
        if since and (e.get("created_at") or 0) < since:
            continue
        a, t = e.get("actor") or {}, e.get("target") or {}
        rows.append(dict(at=e.get("created_at"), action=e.get("action"), result=e.get("result"),
                         by=a.get("display_name"), key=a.get("api_key_id"), target=t.get("label") or t.get("id"),
                         target_type=t.get("type")))
    rows.sort(key=lambda r: (r["at"] or 0, r["action"] or ""))
    return rows


def audit_step(you: CLI, sb: dict, echo: bool = True) -> dict:
    owner = owner_cli(you, sb)
    rows = audit_rows(owner, sb.get("created_at"), echo=echo)
    emit("text", "\n  " + f"{'time (UTC)':<21}{'action':<26}{'by':<12}{'key':<8}target\n  " + "\n  ".join(
        f"{iso(r['at']):<21}{r['action'] or '':<26}{(r['by'] or '')[:11]:<12}{str(r['key'] or ''):<8}{r['target'] or ''}"
        for r in rows))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    key_updates = counts.get("service_account:update", 0)
    note(f"{len(rows)} event(s) since the sandbox was created: " + ", ".join(f"{k} ×{v}" for k, v in counts.items()))
    note(f"service_account:update ×{key_updates}: the log says the key changed and who changed it, not what "
         "changed — the new allowlist / status is read with `api-key get`")
    failed = (owner.run("audit-log", "list", "--result", "failed", "--page-size", "100", echo=echo) or {}).get("items") or []
    failed = [e for e in failed if (e.get("created_at") or 0) >= (sb.get("created_at") or 0)]
    led = LEDGER or read_ledger()
    refused = [x for x in led if not x["allowed"]]
    by_why: dict[str, int] = {}
    for x in refused:
        by_why[x["why"]] = by_why.get(x["why"], 0) + 1
    note(f"refused calls in the audit log: {len(failed)} · in the demo's access ledger: {len(refused)} of "
         f"{len(led)} agent calls (" + ", ".join(f"{k} {v}" for k, v in sorted(by_why.items())) + ")")
    emit("text", "\n  access ledger — refused calls (out/access-ledger.jsonl, kept by the demo)\n  " + "\n  ".join(
        f"  {iso(x['at'])}  {x['agent']:<20} {x['op']:<7} {(x['project'] or '—'):<20} HTTP {x['status']} "
        f"{x['why']:<8}" + (f" from {x['ip']}" if x["ip"] else "") for x in refused))
    out = dict(rows=rows, counts=counts, audit_failed=len(failed), ledger_calls=len(led), ledger_refused=len(refused),
               refused_by=by_why, refused=refused)
    emit("audit", "", **out)
    return out


def write_ledger() -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / "access-ledger.jsonl").open("w", encoding="utf-8") as fh:
        for x in LEDGER:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")


def read_ledger() -> list[dict]:
    p = OUT / "access-ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --------------------------------------------------------------------------- report

def write_report(contract: dict, sb: dict, res: dict) -> dict:
    doc = dict(company=contract["company"]["name"], partner=contract["company"]["partner"],
               generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               sandbox=dict(team_id=sb["team_id"], team_name=sb["team_name"], workspace=sb.get("workspace")),
               egress=sb.get("egress"), keys=res["keys"], partner_check=res["partner"], suspend=res["suspend"],
               egress_change=res["egress"], audit=res["audit"])
    OUT.mkdir(exist_ok=True)
    (OUT / "contract-review.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    p, s, e, a = res["partner"], res["suspend"], res["egress"], res["audit"]
    lines = [f"# {doc['company']} × {doc['partner']} — agent memory contract review", "",
             f"Generated {doc['generated_at']} against a sandbox org team ({sb['team_id']}). Every row is a real call "
             "made with that agent's own API key; the server's answer is recorded as given.", "",
             "## Keys", "", "| key | agent | status | allowed from |", "|---|---|---|---|"]
    for k in e["keys"]:
        lines.append(f"| {k['name']} | {k['owner']} | {k['status']} | {', '.join(k['allowed']) or 'anywhere'} |")
    k4 = res["keys"]["partner"]
    lines += ["", "## Pinned to its network", "",
              f"kestrel-broker's key, pinned to {k4['cidr']}, used from this machine: HTTP {k4['status']} "
              f"{k4['code']} “{k4['message']}”. harborline-dispatch's key is pinned to that IP ({doc['egress']}).", "",
              f"## Contract check — {p['matched']}/{p['total']} as the contract says", "",
              "| agent | op | memory | answer |", "|---|---|---|---|"]
    for x in p["cells"]:
        ans = "✓" if x["allowed"] else f"✗ {x['status']} {x['code']}"
        lines.append(f"| {x['agent']} | {x['op']} | {x['project']} | {ans}{'' if x['match'] else ' (differs)'} {x['detail']} |")
    lines += ["", "## Suspended and resumed", "",
              f"{s['reason']}: refused {s['off']['after_s']} s after `--status disabled` — HTTP {s['off']['status']} "
              f"“{s['off']['message']}”; {s['kept']}/{s['wrote']} of its facts stayed readable. {s['resolved']}: the "
              f"same key answered rc {s['on']['rc']} {s['on']['after_s']} s after `--status enabled`.", "",
              "## The partner's network changed", "",
              f"{e['reason']}: HTTP {e['partner']['status']} “{e['partner']['message']}”; harborline-dispatch from the "
              f"same machine: rc {e['dispatch_rc']}.", "",
              "## Audit log", "", "| time (UTC) | action | by | key | target |", "|---|---|---|---|---|"]
    for r in a["rows"]:
        lines.append(f"| {iso(r['at'])} | {r['action']} | {r['by']} | {r['key']} | {r['target']} |")
    lines += ["", f"Refused calls in the audit log: {a['audit_failed']}. In the demo's access ledger: "
              f"{a['ledger_refused']} of {a['ledger_calls']} (see out/access-ledger.jsonl)."]
    (OUT / "contract-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return doc


def show_report() -> dict:
    p = OUT / "contract-review.json"
    if not p.exists():
        die("no contract review yet; run `python3 demo.py` first")
    doc = json.loads(p.read_text(encoding="utf-8"))
    pc = doc["partner_check"]
    emit("text", f"\n  {doc['company']} × {doc['partner']} — contract review ({doc['generated_at']})\n"
                 f"  {pc['matched']}/{pc['total']} calls answered as the contract says · this machine: {doc['egress']}\n"
                 "  full report: out/contract-review.md · every agent call: out/access-ledger.jsonl")
    emit("report", "", **doc)
    return doc


# --------------------------------------------------------------------------- cleanup

def cleanup(you: CLI, quiet: bool = False) -> None:
    sb = read_sandbox()
    _PENDING.clear()
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
    note("deleted the sandbox and agent profiles under .memorylake-demo/ (your own login stays)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(you: CLI, reset: bool = False) -> dict:
    contract = load_contract()
    LEDGER.clear()
    t0 = time.time()
    emit("intro", "", company=contract["company"], agents=contract["agents"], network=contract["partner_network"],
         projects=[dict(slug=p["slug"], name=p["name"], description=p["description"], shared=p["shared"])
                   for p in contract["projects"]])
    banner(2, TOTAL, "A sandbox org team — the only kind of team that can have members")
    if reset or read_sandbox():
        note("--reset" if reset else "found a sandbox team from an earlier run")
        note("teams, members and keys are one-shot, so every run starts from nothing: deleting it first")
        cleanup(you, quiet=True)
    sb = create_sandbox(you, contract)
    banner(3, TOTAL, "Two memories, two agents — the contract says who reaches what")
    setup_step(you, sb, contract)
    banner(4, TOTAL, "Each key pinned to its agent's network")
    ks = keys_step(you, sb, contract)
    banner(5, TOTAL, "Harborline's dispatch agent writes the shipments")
    dispatch_step(you, sb, contract)
    banner(6, TOTAL, "Kestrel's agent joins the shared memory — and only the shared memory")
    pt = partner_step(you, sb, contract)
    banner(7, TOTAL, "A key may have leaked: suspend it, keep its memory, resume the same key")
    su = suspend_step(you, sb, contract)
    banner(8, TOTAL, "Kestrel's network changes: the contract drops this machine")
    eg = egress_step(you, sb, contract)
    banner(9, TOTAL, "Audit log for contract changes, access ledger for every call")
    write_ledger()
    au = audit_step(you, sb)
    write_report(contract, sb, dict(keys=ks, partner=pt, suspend=su, egress=eg, audit=au))
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   egress=sb.get("egress"), pinned_status=ks["partner"]["status"], pinned_code=ks["partner"]["code"],
                   matched=pt["matched"], total=pt["total"], rank=pt["rank"],
                   suspend_ok=su["ok"], off_s=su["off"]["after_s"], off_status=su["off"]["status"],
                   on_s=su["on"]["after_s"], kept=su["kept"], wrote=su["wrote"], egress_ok=eg["ok"],
                   egress_status=eg["partner"]["status"], audit_events=len(au["rows"]), audit_counts=au["counts"],
                   audit_failed=au["audit_failed"], ledger_calls=au["ledger_calls"], ledger_refused=au["ledger_refused"],
                   refused_by=au["refused_by"])
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    emit("summary", "", **summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "report", "audit", "cleanup"],
                    help="run = full demo (default); report = print the last contract review; audit = read the "
                         "sandbox team's audit log and the access ledger again; cleanup = delete the sandbox team "
                         "and its local profiles")
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
        banner(2, 2, "The sandbox team's audit log and the access ledger")
        sb = read_sandbox()
        if not sb:
            die("no sandbox team — run `python3 demo.py` first (cleanup deletes it, and its audit log with it)")
        audit_step(you, sb)
        return
    run_pipeline(you, reset=args.reset)
    print("\nDone. The sandbox team is still there so you can look around: `python3 demo.py audit` reads its audit "
          "log again,\n`python3 demo.py report` prints the contract review (out/contract-review.md), "
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

#!/usr/bin/env python3
"""
Design system memory for design studios — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Kiteline Studio (fictional, eight people) keeps its design system in two layers:
the studio's own system (tokens, rules, banned patterns — one project), and one
project per client holding only what that client changes (Orchard Bank: square
corners, forest green, a serif headline face, no shadows; Pell & Finch: burnt
orange, a denser grid, their catalogue face).

An AI tool that reads one layer gets it wrong in one direction: the client
project alone has no spacing or body-text rules at all, so the tool invents
them.  An AI tool that reads both layers gets both values back for every token
the client overrides (8px and 0px for the same corner radius) — search ranks by
relevance and has no notion of which layer wins.  Read layered — every token
from the studio system, replaced by the client's override where there is one —
yesterday's three AI drafts are reviewed line by line, each finding citing the
layer, version and reason it comes from.  Then the studio changes one token
today: the client that does not override it inherits the change at once (a
draft that passed yesterday fails today), the client that overrides it does not
move, and `fact trace` keeps the history.

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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-dsm"  # memorylake-usecases / design-system-memory


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


def clients(story: dict) -> list[str]:
    return [k for k, l in story["layers"].items() if l.get("client")]


def client_key(story: dict, name: str) -> str:
    name = name.lower()
    for k in clients(story):
        l = story["layers"][k]
        if name in (k, l["short"].lower(), l["label"].lower()):
            return k
    die(f"unknown client {name!r}; this demo has: {', '.join(clients(story))}")
    return ""


def label(story: dict, layer: str) -> str:
    return story["layers"][layer]["label"]


def owner_text(story: dict, layer: str) -> str:
    return f"{story['studio'].split()[0]} studio system" if layer == "base" else f"{label(story, layer)} override"


def token_text(story: dict, t: dict, version: int) -> str:
    who = story["people"][t["by"]]["name"]
    return (f"{owner_text(story, t['layer'])}: token {t['token']} = {t['value']} "
            f"(v{version}, decided {t['decided']} by {who}). Why: {t['why']}.")


def banned_text(story: dict, b: dict) -> str:
    who = story["people"][b["by"]]["name"]
    return (f"{owner_text(story, b['layer'])}: banned pattern {b['pattern']} - no {b['label']} "
            f"(decided {b['decided']} by {who}). Why: {b['why']}.")


def token_meta(story: dict, t: dict, version: int, previous: dict | None = None) -> dict:
    m = dict(kind="token", key=f"token:{t['token']}", token=t["token"], value=t["value"], layer=t["layer"],
             version=version, decided=t["decided"], by=story["people"][t["by"]]["name"], why=t["why"])
    if previous:
        m["previous"] = previous
    return m


def banned_meta(story: dict, b: dict) -> dict:
    return dict(kind="banned", key=f"banned:{b['pattern']}", pattern=b["pattern"], label=b["label"], layer=b["layer"],
                decided=b["decided"], by=story["people"][b["by"]]["name"], why=b["why"])


# --------------------------------------------------------------------------- reading memory

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


def list_facts(cli: CLI, project_ids: list[str], echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", ",".join(project_ids)], echo=echo)


def find(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> list[dict]:
    """The pinned design-system facts (token / banned), with their metadata flattened in."""
    out = []
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("kind") in ("token", "banned"):
            out.append(dict(m, id=f["id"], text=f.get("fact")))
    return out


def as_of(e: dict, day: str) -> dict | None:
    """The version of an entry that was in force on `day` (ISO date): the current one, the previous one
    if the current was decided later, or None if the entry did not exist yet."""
    if e["decided"] <= day:
        return e
    p = e.get("previous")
    if p and p["decided"] <= day:
        return dict(e, **p)
    return None


def resolve(story: dict, ents: list[dict], client: str, day: str) -> dict:
    """Every token from the studio system, replaced by the client's override where there is one."""
    tokens: dict[str, dict] = {}
    for e in ents:
        if e["kind"] != "token" or e["layer"] not in ("base", client):
            continue
        v = as_of(e, day)
        if v:
            tokens.setdefault(e["token"], {})[e["layer"]] = v
    out = {}
    for tok, both in sorted(tokens.items()):
        win = both.get(client) or both.get("base")
        out[tok] = dict(token=tok, value=win["value"], layer=win["layer"], version=win["version"], decided=win["decided"],
                        by=win["by"], why=win["why"], id=win["id"],
                        overrides=both["base"]["value"] if client in both and "base" in both else None)
    banned = [b for b in ents if b["kind"] == "banned" and b["layer"] in ("base", client) and as_of(b, day)]
    return dict(client=client, day=day, tokens=out, banned=banned)


def source(story: dict, r: dict) -> str:
    return f"{label(story, r['layer'])} v{r['version']}, {r['decided']}"


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    for k, l in story["layers"].items():
        proj = find(cli, l["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", l["name"], "--custom-id", cid(l["key"]),
                           "--description", l["description"], scoped=True)
            note(f"created “{l['name']}” → {proj['id']}")
        else:
            note(f"“{l['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[k] = proj["id"]
    lines = [f"\n  {story['layers']['base']['name']:<32} {ids['base']}  (every client system starts from this)"]
    lines += [f"  {story['layers'][k]['name']:<32} {ids[k]}  (only what this client changes)" for k in clients(story)]
    emit("text", "\n".join(lines))
    emit("setup", "", ids=ids, layers={k: dict(name=l["name"], label=l["label"], client=bool(l.get("client")),
                                                description=l["description"]) for k, l in story["layers"].items()})
    return ids


# --------------------------------------------------------------------------- step 3: the system in memory

def pin(cli: CLI, project_id: str, items: list[tuple[str, dict]], what: str, echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key, so an entry the studio has
    since revised is not pinned again) — one `fact add` for all of them, then `fact update --metadata` on
    each (fact add takes no metadata). Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, [project_id], echo=False):
        k = (f.get("metadata") or {}).get("key")
        if k:
            live[k] = f
    out, missing = {}, []
    for text, meta in items:
        f = live.get(meta["key"])
        if f is None:
            missing.append((text, meta))
        else:
            out[meta["key"]] = dict(id=f["id"], new=False)
    if missing:
        res = cli.run("fact", "add", "--project", project_id, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, "--project", project_id, "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way ({what})")
    return out


def show_layer(story: dict, layer: str, ents: list[dict]) -> None:
    mine = [e for e in ents if e["layer"] == layer]
    toks = sorted((e for e in mine if e["kind"] == "token"), key=lambda e: e["token"])
    bans = [e for e in mine if e["kind"] == "banned"]
    lines = [f"\n  {story['layers'][layer]['name']} — {len(toks)} token(s), {len(bans)} banned pattern(s)"]
    for e in toks:
        lines.append(f"    {e['token']:<15} {clip(e['value'], 30):<31} v{e['version']} {e['decided']}  why: {clip(e['why'], 52)}")
    for e in bans:
        lines.append(f"    banned         {e['pattern']:<31}    {e['decided']}  why: {clip(e['why'], 52)}")
    emit("text", "\n".join(lines))


def pin_system(cli: CLI, ids: dict, story: dict) -> list[dict]:
    note("each token is one fact with metadata (token, value, layer, version, decided, by, why); the studio system goes "
         "on its project, each client's overrides on the client's project — nothing else")
    fresh = 0
    for layer in story["layers"]:
        items = [(token_text(story, t, 1), token_meta(story, t, 1)) for t in story["tokens"] if t["layer"] == layer]
        items += [(banned_text(story, b), banned_meta(story, b)) for b in story["banned"] if b["layer"] == layer]
        got = pin(cli, ids[layer], items, "token, value, layer, version, decided, why")
        fresh += sum(1 for g in got.values() if g["new"])
    if not fresh:
        note("the design system was pinned by an earlier run; reading it back")
    ents = entries(list_facts(cli, [ids[k] for k in story["layers"]]))
    for layer in story["layers"]:
        show_layer(story, layer, ents)
    over = {k: sorted(e["token"] for e in ents if e["layer"] == k and e["kind"] == "token") for k in clients(story)}
    emit("text", "\n" + "\n".join(f"  {label(story, k)} overrides {len(v)} of the studio's tokens: {', '.join(v)}" for k, v in over.items()))
    emit("system", "", entries=ents, overrides=over)
    return ents


# --------------------------------------------------------------------------- step 4: how an AI tool reads it

def search(cli: CLI, q: str, project_ids: list[str], top_k: int, by_id: dict, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", ",".join(project_ids), "--types", "fact", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        e = by_id.get(f.get("id")) or {}
        out.append(dict(rank=i, id=f.get("id"), text=f.get("fact") or f.get("content"), layer=e.get("layer"),
                        kind=e.get("kind"), token=e.get("token") or e.get("pattern"), value=e.get("value")))
    return out


def read_both_ways(cli: CLI, ids: dict, story: dict, ents: list[dict], client: str, q: str, token: str | None,
                   echo: bool = True) -> dict:
    by_id = {e["id"]: e for e in ents}
    only = search(cli, q, [ids[client]], 5, by_id, echo=echo)
    both = search(cli, q, [ids["base"], ids[client]], 8, by_id, echo=echo)
    if token is None:  # Ask: take the token of the first token hit in the layered scope
        token = next((h["token"] for h in both if h["kind"] == "token"), None)
    hit_only = [h for h in only if h["token"] == token and h["kind"] == "token"]
    hit_both = [h for h in both if h["token"] == token and h["kind"] == "token"]
    return dict(client=client, q=q, token=token, only=only, both=both, hit_only=hit_only, hit_both=hit_both,
                ambiguous=len({h["value"] for h in hit_both}) > 1, first=hit_both[0] if hit_both else None)


def show_both_ways(story: dict, r: dict, resolved: dict | None = None) -> None:
    c = label(story, r["client"])
    lines = [f"\n  “{r['q']}”  ({r['token'] or 'no token found'})"]
    if r["hit_only"]:
        h = r["hit_only"][0]
        lines.append(f"    {c + ' only (top 5)':<30} ✓ {h['value']}  ({label(story, h['layer'])}, #{h['rank']})")
    else:
        lines.append(f"    {c + ' only (top 5)':<30} ✗ no {r['token']} in this scope ({len(r['only'])} hits, none about it) — the tool has to guess")
    if r["hit_both"]:
        vals = " · ".join(f"{h['value']} ({label(story, h['layer'])}, #{h['rank']})" for h in r["hit_both"])
        mark = "⚠" if r["ambiguous"] else "✓"
        lines.append(f"    {'studio + ' + c + ' (top 8)':<30} {mark} {vals}")
        if r["ambiguous"]:
            f = r["first"]
            lines.append(f"    {'':<30}   two answers for one token; a tool taking the first match uses {f['value']} "
                         f"({label(story, f['layer'])})")
    else:
        lines.append(f"    {'studio + ' + c + ' (top 8)':<30} ✗ {r['token']} not in the top 8")
    if resolved and r["token"] in resolved["tokens"]:
        t = resolved["tokens"][r["token"]]
        lines.append(f"    {'layered (client wins)':<30} ✓ {t['value']}  ({source(story, t)})")
    emit("text", "\n".join(lines))


def ai_reads(cli: CLI, ids: dict, story: dict, ents: list[dict]) -> dict:
    client = story["questions"][0]["client"]
    c = label(story, client)
    note(f"an AI design tool asks memory before it generates. Two ways to scope that search for {c}: "
         f"the client project alone, or the studio system + the client project")
    rows = []
    for i, q in enumerate(q for q in story["questions"] if q["client"] == client):
        r = read_both_ways(cli, ids, story, ents, client, q["q"], q["token"], echo=i == 0)
        show_both_ways(story, r)
        rows.append(r)
    if len(rows) > 1:
        note(f"… the other {len(rows) - 1} question(s) ran the same two searches")
    base = sorted(e["token"] for e in ents if e["layer"] == "base" and e["kind"] == "token")
    over = sorted(e["token"] for e in ents if e["layer"] == client and e["kind"] == "token")
    missing = [t for t in base if t not in over]
    emit("text", f"\n  {c} alone holds {len(over)} token(s): the other {len(missing)} ({', '.join(missing)}) are not in its scope at all."
                 f"\n  Studio + {c} holds two values for each of those {len(over)}, and search ranks by relevance — it has no notion of which layer wins.")
    out = dict(client=client, rows=rows, missing=missing, overridden=over)
    emit("ai_reads", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: layered + review

def norm(v: str) -> str:
    return re.sub(r"\s+", "", str(v)).lower()


def review(story: dict, ents: list[dict], draft: dict, day: str) -> dict:
    client = draft["client"]
    res = resolve(story, ents, client, day)
    others = {k: resolve(story, ents, k, day) for k in clients(story) if k != client}
    findings = []
    for tok, val in draft["tokens"].items():
        r = res["tokens"].get(tok)
        if r is None:
            findings.append(dict(item=tok, used=val, ok=None, why="no such token in the system"))
            continue
        ok = norm(val) == norm(r["value"])
        why = ""
        if not ok:
            ver = f"v{r['version']}, {r['decided']}"
            why = (f"{label(story, client)} overrides it: {r['value']} ({ver})" if r["layer"] == client
                   else f"{label(story, client)} inherits the studio's {r['value']} ({ver})")
            bleed = [k for k, o in others.items() if (o["tokens"].get(tok) or {}).get("layer") == k
                     and norm(o["tokens"][tok]["value"]) == norm(val)]
            if bleed:
                why += f" — {val} is {label(story, bleed[0])}'s"
            elif r.get("overrides") and norm(r["overrides"]) == norm(val):
                why += f" — {val} is the studio value it replaces"
            else:
                base = next((e for e in ents if e["kind"] == "token" and e["layer"] == "base" and e["token"] == tok), None)
                prev = (base or {}).get("previous")
                if prev and norm(prev["value"]) == norm(val) and r["layer"] == "base":
                    why += f" — {val} was the studio value before v{base['version']}"
        findings.append(dict(item=tok, used=val, ok=ok, value=r["value"], layer=r["layer"], source=source(story, r),
                             fact=r["id"], why=why or f"{source(story, r)}"))
    for p in draft["patterns"]:
        b = next((b for b in res["banned"] if b["pattern"] == p), None)
        elsewhere = [e for e in ents if e["kind"] == "banned" and e["pattern"] == p and e["layer"] not in ("base", client)]
        if b:
            findings.append(dict(item=f"pattern {p}", used=p, ok=False, layer=b["layer"], fact=b["id"],
                                 why=f"banned by {label(story, b['layer'])} ({b['decided']}): {b['why']}"))
        else:
            findings.append(dict(item=f"pattern {p}", used=p, ok=True, why=f"allowed for {label(story, client)}"
                                 + (f" (banned only for {label(story, elsewhere[0]['layer'])})" if elsewhere else "")))
    bad = sum(1 for f in findings if f["ok"] is False)
    return dict(id=draft["id"], client=client, title=draft["title"], made_by=draft["made_by"], day=day,
                findings=findings, bad=bad, ok=len(findings) - bad)


def show_review(story: dict, rv: dict) -> None:
    head = "✓ passes" if not rv["bad"] else f"✗ {rv['bad']} finding(s)"
    lines = [f"\n  {rv['id']}  {rv['title']}  — {head}", f"      made by {rv['made_by']}"]
    for f in rv["findings"]:
        mark = "✓" if f["ok"] else "?" if f["ok"] is None else "✗"
        lines.append(f"    {mark} {f['item']:<20} {clip(f['used'], 22):<23} {clip(f['why'], 120)}")
    emit("text", "\n".join(lines))


def show_resolved(story: dict, res: dict) -> None:
    c = label(story, res["client"])
    lines = [f"\n  {c} as of {res['day']} — studio system, client overrides on top ({len(res['tokens'])} tokens):"]
    for t in res["tokens"].values():
        tag = f"{c} override (studio: {t['overrides']})" if t["layer"] != "base" else "inherited from studio"
        lines.append(f"    {t['token']:<15} {clip(t['value'], 30):<31} {tag:<44} v{t['version']} · {t['id']}")
    bans = ", ".join("%s (%s)" % (b["pattern"], label(story, b["layer"])) for b in res["banned"])
    lines.append(f"    banned: {bans}")
    emit("text", "\n".join(lines))


def layered(cli: CLI, ids: dict, story: dict) -> dict:
    day = story["drafts_made"]
    note("read layered instead: `fact list` over the studio system and the client together (every page, not a top-k), "
         "then every token from the studio, replaced by the client's override where there is one")
    systems = {}
    for i, k in enumerate(clients(story)):
        ents = entries(list_facts(cli, [ids["base"], ids[k]], echo=i == 0))
        systems[k] = resolve(story, ents, k, day)
        show_resolved(story, systems[k])
    ents = entries(list_facts(cli, [ids[k] for k in story["layers"]], echo=False))
    note(f"yesterday ({day}) three AI tools produced drafts; each is reviewed against its client's layered system as of that day")
    reviews = [review(story, ents, d, day) for d in story["drafts"]]
    for rv in reviews:
        show_review(story, rv)
    emit("text", "\n  " + "   ".join(f"{rv['id']} {'✓' if not rv['bad'] else '✗ ' + str(rv['bad'])}" for rv in reviews))
    out = dict(day=day, systems=systems, reviews=reviews)
    emit("layered", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: the studio changes a token

def trace(cli: CLI, project_id: str, fact_id: str, echo: bool = True) -> list[dict]:
    res = cli.run("fact", "trace", fact_id, "--project", project_id, scoped=True, echo=echo) or {}
    out = []
    for h in res.get("trace") or []:
        out.append(dict(event=h.get("event"), how=h.get("source_kind"), at=h.get("timestamp"),
                        text=h.get("new_fact"), old=h.get("old_fact")))
    return out


def evolve(cli: CLI, ids: dict, story: dict, before: dict) -> dict:
    u = story["update"]
    ents = entries(list_facts(cli, [ids["base"]], echo=False))
    e = next(x for x in ents if x["kind"] == "token" and x["token"] == u["token"])
    if e["value"] == u["value"]:
        note(f"{u['token']} was changed to {u['value']} by an earlier run; reading it back")
        changed = False
    else:
        version = int(e["version"]) + 1
        prev = dict(value=e["value"], version=e["version"], decided=e["decided"], by=e["by"], why=e["why"])
        t = dict(u, layer="base")
        note(f"{story['today']}: {story['people'][u['by']]['name']} changes {u['token']} in the studio system, "
             f"{e['value']} → {u['value']} — one `fact update` on the studio project, nothing on any client project")
        cli.run("fact", "update", e["id"], "--project", ids["base"], "--text", token_text(story, t, version),
                "--metadata", json.dumps(token_meta(story, t, version, previous=prev)), scoped=True)
        changed = True
    ents = entries(list_facts(cli, [ids[k] for k in story["layers"]]))
    rows = []
    for k in clients(story):
        old = before["systems"][k]["tokens"][u["token"]]
        new = resolve(story, ents, k, story["today"])["tokens"][u["token"]]
        rows.append(dict(client=k, old=old["value"], new=new["value"], layer=new["layer"], moved=old["value"] != new["value"]))
    lines = [f"\n  {u['token']} per client, {story['drafts_made']} → {story['today']}:"]
    for r in rows:
        why = (f"inherits the studio value" if r["layer"] == "base"
               else f"{label(story, r['client'])} overrides it")
        lines.append(f"    {label(story, r['client']):<14} {r['old']:>6} → {r['new']:<6} {'changed' if r['moved'] else 'unchanged'} — {why}")
    emit("text", "\n".join(lines))
    note("the same three drafts, reviewed against today's system")
    reviews = [review(story, ents, d, story["today"]) for d in story["drafts"]]
    flips = []
    for rv in reviews:
        was = next(x for x in before["reviews"] if x["id"] == rv["id"])
        if bool(was["bad"]) != bool(rv["bad"]) or was["bad"] != rv["bad"]:
            show_review(story, rv)
            flips.append(dict(id=rv["id"], before=was["bad"], after=rv["bad"]))
    emit("text", "\n  " + "   ".join(
        f"{rv['id']} {'✓' if not was['bad'] else '✗ ' + str(was['bad'])} → {'✓' if not rv['bad'] else '✗ ' + str(rv['bad'])}"
        for rv, was in ((rv, next(x for x in before["reviews"] if x["id"] == rv["id"])) for rv in reviews)))
    note("how the system changed is kept: `fact trace` on the studio's token")
    e = next(x for x in ents if x["kind"] == "token" and x["layer"] == "base" and x["token"] == u["token"])
    hist = trace(cli, ids["base"], e["id"])
    lines = [f"\n  {u['token']} history ({e['id']}), newest first:"]
    for h in hist:
        lines.append(f"    {str(h['event']):<7} {str(h['how'] or ''):<7} {str(h['at'] or '')[:19]}  {clip(h['text'] or '', 104)}")
        if h["old"]:
            lines.append(f"    {'':<35}  was: {clip(h['old'], 99)}")
    emit("text", "\n".join(lines))
    out = dict(token=u["token"], changed=changed, rows=rows, reviews=reviews, flips=flips, history=hist, fact=e["id"],
               update=u)
    emit("evolved", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for l in story["layers"].values():
        proj = find(cli, l["key"], echo=not quiet)
        if proj:
            for f in list_facts(cli, [proj["id"]], echo=False):
                cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
            cli.run("proj", "delete", proj["id"], scoped=True)
    note("deleted the studio system, both client systems and every fact on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — the studio system, and one project per client for what that client changes")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "The design system in memory — tokens, banned patterns, and why, per layer")
    ents = pin_system(cli, ids, story)
    banner(4, TOTAL, "How an AI tool reads it — the client alone, or the studio and the client together")
    reads = ai_reads(cli, ids, story, ents)
    banner(5, TOTAL, f"Layered: the client wins, token by token — yesterday's AI drafts reviewed ({story['drafts_made']})")
    lay = layered(cli, ids, story)
    banner(6, TOTAL, f"The studio changes one token today — who inherits it, who doesn't ({story['today']})")
    evo = evolve(cli, ids, story, lay)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   reads=[dict(q=r["token"], only=[h["value"] for h in r["hit_only"]],
                               both=[(h["value"], h["layer"], h["rank"]) for h in r["hit_both"]]) for r in reads["rows"]],
                   before={rv["id"]: rv["bad"] for rv in lay["reviews"]}, after={rv["id"]: rv["bad"] for rv in evo["reviews"]},
                   inherit={r["client"]: [r["old"], r["new"]] for r in evo["rows"]}, trace=[h["event"] for h in evo["history"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    ids = {}
    for k, l in story["layers"].items():
        proj = find(cli, l["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[k] = proj["id"]
    return ids


def ask(cli: CLI, ids: dict, client: str, q: str, echo: bool = True) -> dict:
    """Any design question for one client: the client-only read, the studio + client read, and the layered answer."""
    story = load_story()
    ents = entries(list_facts(cli, [ids[k] for k in story["layers"]], echo=False))
    r = read_both_ways(cli, ids, story, ents, client, q, None, echo=echo)
    res = resolve(story, ents, client, story["today"])
    r["layered"] = res["tokens"].get(r["token"]) if r["token"] else None
    if r["layered"]:
        r["layered"] = dict(r["layered"], source=source(story, r["layered"]))
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "system", "review", "ask", "cleanup"],
                    help="run = full demo (default); system CLIENT = the client's layered system today; review = today's "
                         "review of the three drafts; ask CLIENT \"QUESTION\" = the three ways to read memory for any "
                         "design question; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="client (orchard / pell) and, for `ask`, the question")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "system" and len(a.args) != 1:
        ap.error("system needs a client: orchard or pell")
    if a.command == "ask" and len(a.args) != 2:
        ap.error("ask needs a client and a question, e.g. ask pell \"What font size is body text?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py ask pell \"How big is body text?\"`, `python3 demo.py system orchard` or "
              "`python3 demo.py review`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "system":
        k = client_key(story, a.args[0])
        banner(2, 2, f"{label(story, k)} — the layered system today")
        ents = entries(list_facts(cli, [ids["base"], ids[k]]))
        show_resolved(story, resolve(story, ents, k, story["today"]))
    elif a.command == "review":
        banner(2, 2, f"The three AI drafts, reviewed against today's layered systems ({story['today']})")
        ents = entries(list_facts(cli, [ids[k] for k in story["layers"]]))
        for d in story["drafts"]:
            show_review(story, review(story, ents, d, story["today"]))
    elif a.command == "ask":
        k = client_key(story, a.args[0])
        banner(2, 2, f"Ask — a design question for {label(story, k)}, read three ways")
        r = ask(cli, ids, k, a.args[1])
        res = resolve(story, entries(list_facts(cli, [ids[x] for x in story["layers"]], echo=False)), k, story["today"])
        show_both_ways(story, r, res)


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

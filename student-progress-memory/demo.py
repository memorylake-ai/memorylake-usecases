#!/usr/bin/env python3
"""
Student progress memory for tutoring businesses — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Learning (fictional) tutors grade 8 math.  Priya Raman has had a
weekly session with Owen Hale since March; Owen left on September 30 and Mara
Lind takes Priya over today, October 14.  Priya's school has a Unit 6 test on
October 29: exponent rules, scientific notation, the Pythagorean theorem.

Every session note, Owen's handover note, the grade 8 skill list and the test
syllabus are MemoryLake facts, word for word, with metadata: which skills a
session assessed (secure / shaky) and which it only practiced.

The quick read is what a tutor's AI assistant does on handover day: search the
student's memory — "what does she struggle with?", "how is she doing with
<each test skill>?" — and report the concerns the hits mention.  It finds the
two weak spots Owen wrote down and flags 0 of the 3 test skills.  Nothing it
says is false.  But the coverage map — every skill on the list against every
assessment on record, plain code over `fact list` and metadata — shows all 3
test skills unverified: exponent rules were practiced and never checked,
scientific notation was last checked 176 days ago, before the summer, and the
Pythagorean theorem was never touched.  No note says "not assessed", so no
search hit can.  The same two readings of Leo, a student whose records do
cover the test, agree.

Step 6 writes back Mara's 15-minute check and a "coverage gap" fact for every
skill still never assessed — the absence becomes something search can find.

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

PREFIX = "mlu-spm"  # memorylake-usecases / student-progress-memory


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


def skills(story: dict) -> dict[str, dict]:
    return {s["id"]: s for s in story["skills"]}


def students(story: dict) -> dict[str, dict]:
    return {s["key"]: s for s in story["students"]}


def test_of(story: dict, student: dict) -> dict:
    return next(t for t in story["tests"] if t["id"] == student["test"])


def days(a: str, b: str) -> int:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days


PROJECTS = ("curriculum", "priya", "leo")


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null, so absent fields are simply left out.
    m = dict(key=f"spm:{r['id']}", record=r["id"], kind=r["kind"], by=r["by"], on=r["on"])
    for k in ("student", "skill", "test", "date"):
        if r.get(k):
            m[k] = r[k]
    for k in ("assessed", "practiced", "skills"):
        if r.get(k):
            m[k] = list(r[k])
    return m


def curriculum_records(story: dict) -> list[dict]:
    out = [dict(id=s["id"], kind="skill", by="Larkspur Learning", on="2026-03-01", skill=s["id"],
                text=f"Grade 8 math skill {s['id']}: {s['name']} (unit {s['unit']}).") for s in story["skills"]]
    out += [dict(id=t["id"], kind="test", by="Larkspur Learning", on="2026-09-01", test=t["id"], date=t["date"],
                 skills=t["skills"], text=t["text"]) for t in story["tests"]]
    return out


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


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", project_id], echo=echo)


def find(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (P01, S10, …), metadata flattened in, plus the fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            r = dict(m, fid=f["id"], body=f.get("fact") or "")
            for k in ("assessed", "practiced", "skills"):
                r[k] = list(m.get(k) or [])
            out[m["record"]] = r
    return out


def read_memory(cli: CLI, pids: dict[str, str], keys=PROJECTS, echo: bool = True) -> dict[str, dict]:
    """Every record on the given projects: one `fact list` per project (every page)."""
    mem = {}
    for k in keys:
        mem.update(entries(list_facts(cli, pids[k], echo=echo)))
    return mem


def as_of(mem: dict, day: str, include_day: bool = True) -> dict:
    return {k: r for k, r in mem.items() if (r["on"] <= day if include_day else r["on"] < day)}


# --------------------------------------------------------------------------- the coverage map (plain code, no model)

UNVERIFIED = ("stale", "practiced", "never")
MARK = {"secure": "✓", "shaky": "✗", "stale": "?", "practiced": "?", "never": "?"}


def coverage(story: dict, mem: dict, student: str, day: str, include_day: bool = True) -> dict:
    """Every skill on the list against every assessment of `student` on record as of `day`."""
    view = as_of(mem, day, include_day)
    notes = sorted((r for r in view.values() if r.get("student") == student and r["kind"] == "session"),
                   key=lambda r: (r["on"], r["record"]))
    sk = skills(story)
    st = students(story)[student]
    t = test_of(story, st)
    rows = []
    for s in story["skills"]:
        checks = [dict(record=n["record"], on=n["on"], result=a.split("=", 1)[1], by=n["by"])
                  for n in notes for a in n["assessed"] if a.split("=", 1)[0] == s["id"]]
        practice = [dict(record=n["record"], on=n["on"], by=n["by"]) for n in notes if s["id"] in n["practiced"]]
        last = checks[-1] if checks else None
        if last is None:
            status = "practiced" if practice else "never"
        elif last["result"] != "secure":
            status = "shaky"
        else:
            status = "stale" if days(last["on"], day) > story["stale_days"] else "secure"
        gap = next((r for r in view.values() if r.get("student") == student and r["kind"] == "gap" and r.get("skill") == s["id"]), None)
        rows.append(dict(skill=s["id"], name=s["name"], unit=s["unit"], status=status, last=last,
                         age=days(last["on"], day) if last else None, practiced=practice, on_test=s["id"] in t["skills"],
                         gap=gap["record"] if gap else ""))
    test_rows = [r for r in rows if r["on_test"]]
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in story["status_names"]}
    return dict(student=student, name=st["name"], day=day, rows=rows, counts=counts, test=dict(t, skills_rows=test_rows),
                unverified=[r["skill"] for r in test_rows if r["status"] in UNVERIFIED],
                shaky=[r["skill"] for r in test_rows if r["status"] == "shaky"], sk={k: v["name"] for k, v in sk.items()})


def why(r: dict, day: str = "") -> str:
    if r["status"] == "never":
        return "no assessment on record" + (f" (gap logged {r['gap']})" if r["gap"] else "")
    if r["status"] == "practiced":
        return "practiced in " + ", ".join(f"{p['record']} {p['on']}" for p in r["practiced"]) + "; no check on record"
    s = f"{r['last']['result']} in {r['last']['record']} {r['last']['on']}"
    if r["status"] == "stale":
        # The summer break ran 2026-06-17 to 2026-08-26 in this story.
        summer = r["last"]["on"] < "2026-06-17" and day > "2026-08-26"
        s += f" — {r['age']} days ago, before the summer" if summer else f" — {r['age']} days ago"
    return s


def map_lines(story: dict, cov: dict, only_test: bool = False) -> list[str]:
    names = story["status_names"]
    lines = [f"    {'skill':<34} {'status':<34} evidence"]
    for r in cov["rows"]:
        if only_test and not r["on_test"]:
            continue
        tag = "  ← on the test" if r["on_test"] and not only_test else ""
        lines.append(f"    {r['skill'] + ' ' + r['name']:<34} {MARK[r['status']] + ' ' + names[r['status']]:<34} {why(r, cov['day'])}{tag}")
    return lines


def map_summary(story: dict, cov: dict) -> str:
    c = cov["counts"]
    t = cov["test"]
    parts = " · ".join(f"{c[k]} {story['status_names'][k]}" for k in story["status_names"] if c[k])
    u = cov["unverified"]
    s = f"  → {cov['name']}, as of {cov['day']}: {parts}\n"
    s += f"  → coverage: {len(u)} of {len(t['skills'])} {t['name']} skills unverified"
    if u:
        s += " (" + "; ".join(f"{x} {story['status_names'][next(r['status'] for r in cov['rows'] if r['skill'] == x)]}" for x in u) + ")"
    if cov["shaky"]:
        s += f"; {len(cov['shaky'])} shaky ({', '.join(cov['shaky'])})"
    return s


# --------------------------------------------------------------------------- the quick read (search, then read the hits)

def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def mentions(story: dict, skill_id: str, text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in skills(story)[skill_id]["keywords"])


def weak(story: dict, sentence: str) -> bool:
    t = sentence.lower()
    return any(w in t for w in story["weak_words"])


def search(cli: CLI, q: str, pids: list[str], top_k: int, mem: dict, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", ",".join(pids), "--types", "fact", "--top-k", str(top_k), scoped=True,
                  echo=echo) or {}
    by_fid = {r["fid"]: r for r in mem.values()}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        r = by_fid.get(f.get("id")) or {}
        out.append(dict(rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or r.get("record") or "?", student=m.get("student") or r.get("student") or "",
                        kind=m.get("kind") or r.get("kind") or "", on=m.get("on") or r.get("on") or ""))
    return out


def read_hits(story: dict, hits: list[dict], skill_id: str) -> dict:
    """What a reader of these hits would say about one skill: a sentence naming it with a weakness word is a concern;
    a mention without one is 'mentioned'; no mention at all is 'nothing on record'."""
    said = [dict(record=h["record"], on=h["on"], rank=h["rank"], sentence=s, weak=weak(story, s))
            for h in hits for s in sentences(h["body"]) if mentions(story, skill_id, s)]
    concern = [x for x in said if x["weak"]]
    verdict = "concern" if concern else "mentioned" if said else "nothing"
    return dict(skill=skill_id, verdict=verdict, said=said, n_mention=len({x["record"] for x in said}), n_hits=len(hits))


QUICK = {"concern": "✗ concern noted", "mentioned": "· mentioned, no concern", "nothing": "· nothing on record"}


def quick_read(cli: CLI, story: dict, pid: str, student: str, mem: dict, day: str, include_day: bool = False,
               echo: bool = True) -> dict:
    st = students(story)[student]
    t = test_of(story, st)
    keep = (lambda h: h["on"] <= day) if include_day else (lambda h: h["on"] < day)
    q = story["questions"]["weak"].format(name=st["name"])
    hits = [h for h in search(cli, q, [pid], 8, mem, echo=echo) if keep(h)]
    spots = {}
    for h in hits:
        for s in sentences(h["body"]):
            if not weak(story, s):
                continue
            for sid in skills(story):
                if mentions(story, sid, s):
                    spots.setdefault(sid, dict(skill=sid, record=h["record"], on=h["on"], sentence=s))
    per = []
    for i, sid in enumerate(t["skills"]):
        qs = story["questions"]["skill"].format(name=st["name"], skill=skills(story)[sid]["name"])
        sh = [h for h in search(cli, qs, [pid], 8, mem, echo=echo and i == 0) if keep(h)]
        per.append(dict(read_hits(story, sh, sid), q=qs, hits=sh))
    if echo and len(t["skills"]) > 1:
        note(f"… and the same search for the other {len(t['skills']) - 1} test skills")
    flagged = [p["skill"] for p in per if p["verdict"] == "concern"]
    return dict(student=student, name=st["name"], q=q, hits=hits, weak=list(spots.values()), per=per, flagged=flagged,
                plan=sorted(set(spots) | set(flagged)), test=t)


def quick_lines(story: dict, qr: dict) -> list[str]:
    sk = skills(story)
    lines = [f"\n  “{qr['q']}” · top {len(qr['hits'])}:"]
    lines += [f"      #{h['rank']:<2} {h['record']:<4} {h['on']}  {clip(h['body'], 92)}" for h in qr["hits"][:4]]
    lines.append("  → weak spots the hits name: " + ("; ".join(f"{w['skill']} {sk[w['skill']]['name']} ({w['record']})" for w in qr["weak"]) or "none"))
    lines.append(f"\n  the {qr['test']['name']} ({qr['test']['date']}), one search per skill:")
    for p in qr["per"]:
        s = f"    {p['skill'] + ' ' + sk[p['skill']]['name']:<34} {QUICK[p['verdict']]:<26}"
        if p["said"]:
            x = p["said"][0]
            s += f" {x['record']} “{clip(x['sentence'], 60)}”"
        else:
            s += f" 0 of {p['n_hits']} hits mention it"
        lines.append(s)
    t = qr["test"]
    lines.append(f"\n  → the quick read: {len(qr['flagged'])} of {len(t['skills'])} test skills flagged; next session plan: "
                 + (", ".join(sk[s]["name"] for s in qr["plan"]) or "nothing specific"))
    return lines


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "student", "kind", "by", "on", "assessed", "practiced", "skill", "skills", "test",
                                  "date", "fid", "body")}


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict[str, str]:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    st = students(story)
    desc = {"curriculum": ("Larkspur curriculum", "Larkspur Learning grade 8 math skill list and test syllabi. Fictional data for "
                           "the MemoryLake student-progress-memory demo.")}
    for k, s in st.items():
        desc[k] = (f"Student {s['full']}", s["description"])
    pids, made = {}, []
    for k in PROJECTS:
        proj = find(cli, k, echo=False)
        if proj is None:
            proj = cli.run("proj", "create", "--name", desc[k][0], "--custom-id", cid(k), "--description", desc[k][1],
                           scoped=True, echo=not made)
            made.append(k)
        pids[k] = proj["id"]
    if len(made) > 1:
        note(f"… and {len(made) - 1} more `proj create` the same way ({', '.join(made[1:])})")
    if len(made) < len(pids):
        note(f"{len(pids) - len(made)} project(s) already exist from an earlier run (pass --reset to start over)")
    lines = ["", f"    {'curriculum':<12} {'the grade 8 skill list + test syllabus':<44} {pids['curriculum']}"]
    for k, s in st.items():
        lines.append(f"    {k:<12} {s['full'] + ', grade ' + str(s['grade']) + ', tutor ' + s['tutor'] + ' since ' + s['since']:<44} {pids[k]}")
    emit("text", "\n".join(lines))
    emit("setup", "", projects=pids, students=story["students"])
    return pids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, project_id: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of
    them, then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, project_id, echo=False):
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
    return out


def intake(cli: CLI, pids: dict[str, str], story: dict) -> dict:
    note("the skill list and the test syllabus go on the curriculum project; every session note and handover note on "
         "its student's project — one fact each, word for word, then `fact update --metadata`: who wrote it, when, "
         "which skills it assessed (secure / shaky) and which it only practiced")
    got = pin(cli, pids["curriculum"], [(r["text"], record_meta(r)) for r in curriculum_records(story)])
    new = sum(1 for g in got.values() if g["new"])
    first = True
    for k in students(story):
        recs = [r for r in story["records"] if r["student"] == k]
        got = pin(cli, pids[k], [(r["text"], record_meta(r)) for r in recs], echo=first)
        first = False
        new += sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    else:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new} new fact(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = read_memory(cli, pids)
    view = as_of(mem, story["today"], include_day=False)
    sk = skills(story)
    t = story["tests"][0]
    lines = [f"\n  curriculum: {sum(1 for r in view.values() if r['kind'] == 'skill')} skills · "
             f"{t['name']} {t['date']}: " + ", ".join(f"{s} {sk[s]['name']}" for s in t["skills"])]
    for k, s in students(story).items():
        rs = sorted((r for r in view.values() if r.get("student") == k), key=lambda r: (r["on"], r["record"]))
        lines.append(f"\n  {s['full']} — {len(rs)} records, {rs[0]['on']} to {rs[-1]['on']}:")
        for r in rs:
            tags = " ".join([a.replace("=", ":") for a in r["assessed"]] + [f"{p}:practiced" for p in r["practiced"]])
            lines.append(f"    {r['record']:<4} {r['on']}  {story['kinds'][r['kind']]:<13} {tags}")
    h = view.get("P-H")
    if h:
        lines.append(f"\n  {h['record']} {h['on']} — {h['by']}:\n    “{h['body']}”")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(view.values(), key=lambda r: (r.get("student") or "", r["on"], r["record"]))],
         kinds=story["kinds"])
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def handover_read(cli: CLI, pids: dict[str, str], story: dict, mem: dict) -> dict:
    note("handover day, the way a tutor's AI assistant reads it: search Priya's memory — what does she struggle with, "
         "and how is she doing with each skill on the coming test — then report what the hits say")
    qr = quick_read(cli, story, pids["priya"], "priya", mem, story["today"])
    emit("text", "\n".join(quick_lines(story, qr)))
    emit("quick_read", "", **qr)
    return qr


# --------------------------------------------------------------------------- step 5: the coverage map

def coverage_map(cli: CLI, pids: dict[str, str], story: dict, qr: dict) -> dict:
    note("the coverage map: every skill on the curriculum project against every assessment on the student's project "
         "— `fact list` (every page) and metadata, plain code. A skill with no assessment on record is not 'fine'; "
         "it is unknown")
    mem = read_memory(cli, pids, keys=("curriculum", "priya"))
    cov = coverage(story, mem, "priya", story["today"], include_day=False)
    lines = [""] + map_lines(story, cov) + ["", map_summary(story, cov)]
    missed = [r for r in cov["test"]["skills_rows"] if r["status"] in UNVERIFIED and r["skill"] not in qr["flagged"]]
    lines.append(f"  → the quick read flagged {len(qr['flagged'])} of {len(cov['test']['skills'])}; "
                 f"{len(missed)} unverified test skill(s) it could not see:")
    qv = {p["skill"]: p for p in qr["per"]}
    for r in missed:
        lines.append(f"      {r['skill']} {r['name']:<26} quick read: {QUICK[qv[r['skill']]['verdict']]:<26} map: {story['status_names'][r['status']]}")
    emit("text", "\n".join(lines))
    note("control — Leo, the same tutor and the same test, whose notes do cover it: both readings")
    mem_l = read_memory(cli, pids, keys=("leo",), echo=True)
    mem_l.update({k: v for k, v in mem.items() if v["kind"] in ("skill", "test")})
    qr_l = quick_read(cli, story, pids["leo"], "leo", mem_l, story["today"], echo=True)
    cov_l = coverage(story, mem_l, "leo", story["today"], include_day=False)
    agree = sorted(qr_l["flagged"]) == sorted(cov_l["shaky"]) and not cov_l["unverified"]
    lines = ["", "    Leo — the quick read:   " + "; ".join(f"{p['skill']} {QUICK[p['verdict']]}" for p in qr_l["per"]),
             "    Leo — the coverage map: " + "; ".join(f"{r['skill']} {MARK[r['status']]} {story['status_names'][r['status']]}"
                                                     for r in cov_l["test"]["skills_rows"]),
             f"  → Leo: {'the two readings agree' if agree else 'the two readings DISAGREE'} — "
             f"{len(qr_l['flagged'])} flagged by the quick read, {len(cov_l['shaky'])} shaky and {len(cov_l['unverified'])} unverified on the map"]
    emit("text", "\n".join(lines))
    out = dict(cov=cov, missed=[r["skill"] for r in missed], control=dict(quick=qr_l, cov=cov_l, agree=agree))
    emit("coverage", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write it back

def write_back(cli: CLI, pids: dict[str, str], story: dict) -> dict:
    day, st = story["today"], students(story)["priya"]
    note(f"{story['new_tutor']['name']} runs the 15-minute check the map asks for; it is pinned like every other "
         "session note — results included, the secure ones too")
    recs = [r for r in story["today_records"] if r["student"] == "priya"]
    got = pin(cli, pids["priya"], [(r["text"], record_meta(r)) for r in recs])
    new_check = any(g["new"] for g in got.values())
    mem = read_memory(cli, pids, keys=("curriculum", "priya"), echo=False)
    cov = coverage(story, mem, "priya", day)
    never = [r for r in cov["rows"] if r["status"] == "never"]
    note("every skill still never assessed becomes a fact that says so — so the next search about it finds the gap "
         "instead of other skills' notes")
    gaps = []
    for r in never:
        rid = f"G-{r['skill']}"
        # Skill first: a gap fact worded "<skill>, <student>: not assessed yet" ranks higher for "how is she doing with <skill>"
        # than "Coverage gap as of …: <student> has no assessment on record for <skill>" (probe: #1 3 of 4 vs 1 of 2).
        text = (f"{r['name'][0].upper() + r['name'][1:]} ({r['skill']}), {st['full']}: not assessed yet. No check on record since tutoring "
                f"began on {st['since']}. " + ("It is on the coming test." if r["on_test"] else "Not on the coming test; check before unit "
                                                + str(r["unit"]) + "."))
        rec = dict(id=rid, student="priya", kind="gap", by=story["new_tutor"]["name"], on=day, skill=r["skill"])
        g = pin(cli, pids["priya"], [(text, record_meta(rec))], echo=not gaps)
        gaps.append(dict(record=rid, skill=r["skill"], name=r["name"], new=any(x["new"] for x in g.values()), body=text))
    mem = read_memory(cli, pids, keys=("curriculum", "priya"), echo=True)
    cov = coverage(story, mem, "priya", day)
    lines = [f"\n  {'pinned' if new_check else 'already pinned by an earlier run'}: " + ", ".join(r["id"] for r in recs)
             + (" · gap facts: " + ", ".join(f"{g['record']} ({g['name']})" for g in gaps) if gaps else "")]
    lines += ["\n  the coverage map again, test skills:"] + map_lines(story, cov, only_test=True) + ["", map_summary(story, cov)]
    sk = skills(story)
    plan = cov["shaky"] + [r["skill"] for r in cov["rows"] if r["status"] == "shaky" and not r["on_test"]]
    lines.append("  → next session plan: " + ", ".join(f"{s} {sk[s]['name']}" for s in plan))
    emit("text", "\n".join(lines))
    probes = []
    asks = [(cov["test"]["skills"][-1], "P13")] + [(g["skill"], g["record"]) for g in gaps[:1]]
    note("and the same two questions again (new wording), now that the check and the gap are on record")
    for i, (sid, want) in enumerate(asks):
        # Not this morning's wording: `search` can answer a repeated question from a cache made before the write.
        q = story["questions"]["after"].format(name=st["name"], skill=sk[sid]["name"])
        # A fact is in `fact list` at once but reaches `search` a few seconds later: ask again until it is indexed.
        t1, tries = time.time(), 0
        while True:
            tries += 1
            hits = search(cli, q, [pids["priya"]], 8, mem, echo=tries == 1)
            rank = next((h["rank"] for h in hits if h["record"] == want), None)
            if rank or tries >= 8:
                break
            time.sleep(4)
        if tries > 1:
            note(f"{want} reached the search index on try {tries} ({int(time.time() - t1)} s)" if rank
                 else f"{want} still not in the search results after {tries} tries ({int(time.time() - t1)} s)")
        probes.append(dict(skill=sid, q=q, want=want, rank=rank, hits=hits, tries=tries))
    lines = []
    for p in probes:
        lines.append(f"\n  “{p['q']}” · top {min(3, len(p['hits']))} of {len(p['hits'])}:")
        lines += [f"      #{h['rank']:<2} {h['record']:<5} {h['on']}  {clip(h['body'], 92)}" for h in p["hits"][:3]]
        lines.append(f"  → {p['want']} is hit #{p['rank']}" if p["rank"] else f"  → {p['want']} is not in the top 8")
    emit("text", "\n".join(lines))
    out = dict(check=[public(mem[r["id"]]) for r in recs if r["id"] in mem], new_check=new_check, gaps=gaps, cov=cov,
               plan=plan, probes=probes)
    emit("written", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask

def ask(cli: CLI, pids: dict[str, str], q: str, echo: bool = True) -> dict:
    """Any question over both students: search's top 8 across their projects, each hit labelled with its student."""
    story = load_story()
    mem = read_memory(cli, pids, keys=tuple(students(story)), echo=False)
    hits = search(cli, q, [pids[k] for k in students(story)], 8, mem, echo=echo)
    return dict(q=q, hits=hits)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    gone = 0
    for k in PROJECTS:
        proj = find(cli, k, echo=not quiet and gone == 0)
        if not proj:
            continue
        for f in list_facts(cli, proj["id"], echo=False):
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = list_facts(cli, proj["id"], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {k} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        gone += 1
    note(f"deleted {gone} project(s) and every record on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a curriculum project and one project per student")
    pids = setup(cli, story, reset)
    cli.ids = dict(projects=pids)  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    n = len(story["skills"]) + len(story["tests"]) + len(story["records"])
    banner(3, TOTAL, f"Intake — {n} records: the skill list, the test syllabus, session and handover notes")
    mem = intake(cli, pids, story)
    banner(4, TOTAL, "Handover day — the quick read")
    qr = handover_read(cli, pids, story, mem)
    banner(5, TOTAL, "The coverage map — every skill against every assessment on record")
    cm = coverage_map(cli, pids, story, qr)
    banner(6, TOTAL, "Write it back — the 15-minute check, and the gaps as facts")
    wb = write_back(cli, pids, story)
    c, cl = cm["cov"], cm["control"]
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   quick_flagged=qr["flagged"], quick_weak=[w["skill"] for w in qr["weak"]],
                   quick_verdicts={p["skill"]: p["verdict"] for p in qr["per"]},
                   map_counts=c["counts"], map_unverified={s: next(r["status"] for r in c["rows"] if r["skill"] == s) for s in c["unverified"]},
                   missed=cm["missed"], control_quick=cl["quick"]["flagged"], control_shaky=cl["cov"]["shaky"],
                   control_unverified=cl["cov"]["unverified"], control_agree=cl["agree"],
                   after_unverified=wb["cov"]["unverified"], after_shaky=wb["cov"]["shaky"], gaps=[g["record"] for g in wb["gaps"]],
                   new_check=wb["new_check"], probe_ranks={p["want"]: p["rank"] for p in wb["probes"]},
                   weak_top1=(qr["hits"][0]["record"] if qr["hits"] else None))
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_projects(cli: CLI) -> dict[str, str]:
    pids = {}
    for k in PROJECTS:
        proj = find(cli, k, echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        pids[k] = proj["id"]
    return pids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "map", "ask", "cleanup"],
                    help="run = full demo (default); map [STUDENT] [DATE] = the coverage map of priya (default) or leo, "
                         "as of a date (YYYY-MM-DD, default today in the story); ask \"QUESTION\" = search's top 8 across "
                         "both students; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="student and/or date (map), or the question (ask)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    student, day = "priya", story["today"]
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"How is Priya doing with systems of equations?\"")
    if a.command == "map":
        for x in a.args:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                day = x
            elif x.lower() in students(story):
                student = x.lower()
            else:
                ap.error(f"map takes a student ({', '.join(students(story))}) and/or a date, e.g. map priya 2026-06-12")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py map leo`, `python3 demo.py map priya 2026-06-12` or "
              "`python3 demo.py ask \"How is Priya doing with systems of equations?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    pids = need_projects(cli)
    if a.command == "map":
        banner(2, 2, f"The coverage map — {students(story)[student]['full']} as of {day}")
        mem = read_memory(cli, pids, keys=("curriculum", student))
        cov = coverage(story, mem, student, day)
        emit("text", "\n".join([""] + map_lines(story, cov) + ["", map_summary(story, cov)]))
    elif a.command == "ask":
        banner(2, 2, "Ask — search's top 8 across both students")
        r = ask(cli, pids, a.args[0])
        lines = [f"\n  “{r['q']}” — top {len(r['hits'])}:"]
        lines += [f"    #{h['rank']:<2} {h['student']:<6} {h['record']:<5} {h['on']}  {clip(h['body'], 88)}" for h in r["hits"]]
        emit("text", "\n".join(lines))


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

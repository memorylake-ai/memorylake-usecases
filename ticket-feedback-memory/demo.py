#!/usr/bin/env python3
"""
Customer feedback memory across tickets — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallyhook (a fictional SaaS for small service businesses) sells in six countries,
and its support team answers every ticket in the customer's language.  Each
ticket's feedback is pinned word for word in a MemoryLake project — German,
Japanese, Portuguese as written — with the account, country, language, plan and
ARR in metadata.  A second project holds the feedback taxonomy: five themes and
a churn signal, each with the words that mark it in each language.  It was
written in June.  Brazil launched in September.

Today, 2026-10-15, product asks what customers want most and customer success
asks which accounts might leave.  The quick read is the dashboard: count each
theme with its English word (`fact list --query`, an exact substring over every
record).  Every record is true and every match is exact — and the dashboard
only counts tickets written in English (plus the Spanish word that happens to
contain "calendar").

The memory read uses the same counting code over the taxonomy as it is in
memory: every word in every language (`fact list --query` each), then `search`
— which ranks by meaning, across languages — for the languages the taxonomy
does not cover.  Portuguese tickets surface there as candidates, and a
Portuguese-speaking agent confirms them.  CSV export is the control: the word
is the same in every language, and the two reads agree.

Step 6 writes it back where the next read will see it: the Portuguese words go
into the taxonomy (`fact update`, old text kept in metadata.previous), and every
non-English ticket gets its themes appended in English.  The same dashboard code
then agrees on every question.

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tfm"  # memorylake-usecases / ticket-feedback-memory


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


def money(n: int) -> str:
    return f"${n:,}"


def ticket_meta(t: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    return dict(key=f"tfm:{t['id']}", kind="ticket", record=t["id"], account=t["account"], country=t["country"],
                lang=t["lang"], plan=t["plan"], arr=t["arr"], on=t["on"], agent=t["agent"])


def theme_text(story: dict, g: dict, terms: dict) -> str:
    by = story["glossary_by"]
    words = "; ".join(f"{lang}: {', '.join(ws)}" for lang, ws in terms.items())
    what = "Feedback theme" if g["kind"] == "request" else "Feedback signal"
    return f"{what} “{g['label']}” ({g['theme']}), set {by['on']} by {by['name']} ({by['role']}). Words that mark it — {words}."


def theme_meta(story: dict, g: dict) -> dict:
    by = story["glossary_by"]
    return dict(key=f"tfm:theme:{g['theme']}", kind="theme", theme=g["theme"], label=g["label"], type=g["kind"],
                terms=g["terms"], search=g["search"], by=by["name"], on=by["on"])


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


def list_facts(cli: CLI, pid: str, echo: bool = True, query: str | None = None) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid] + (["--query", query] if query else []), echo=echo)


def ordered(themes: dict) -> dict:
    order = [g["theme"] for g in load_story()["glossary"]]
    return {t: themes[t] for t in sorted(themes, key=lambda t: order.index(t) if t in order else 99)}


def entries(facts: list[dict]) -> dict[str, dict]:
    """Records by their key (tickets by ticket id, themes by theme id), metadata flattened in, plus fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        k = m.get("record") or m.get("theme")
        if k:
            out[k] = dict(m, fid=f["id"], body=f.get("fact") or "", created=f.get("created_at") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


PUBLIC = ("record", "account", "country", "lang", "plan", "arr", "on", "agent", "fid", "body", "rank", "previous", "themes")


def public(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {k: r.get(k) for k in PUBLIC}


def search(cli: CLI, q: str, pid: str, top_k: int = 10, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", lang=m.get("lang") or "?", account=m.get("account") or "?"))
    return out


# --------------------------------------------------------------------------- the two reads

def census(cli: CLI, pid: str, words: list[str], tickets: dict, echo: bool = True) -> tuple[set, dict]:
    """`fact list --query` per word (exact, case-insensitive substring, every page). Returns (ticket ids, word → ids)."""
    by_fid = {r["fid"]: k for k, r in tickets.items()}
    words = list(dict.fromkeys(words))
    # Read-only and independent: the first one echoes, the rest run side by side.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(lambda iw: list_facts(cli, pid, echo=echo and iw[0] == 0, query=iw[1]), enumerate(words)))
    hits, per = set(), {}
    for w, facts in zip(words, got):
        ids = {by_fid[f["id"]] for f in facts if f["id"] in by_fid}
        per[w] = sorted(ids)
        hits |= ids
    if echo and len(per) > 1:
        note(f"… and {len(per) - 1} more `fact list --query` the same way ({', '.join(list(per)[1:])})")
    return hits, per


def quick_read(cli: CLI, pid: str, themes: dict, tickets: dict, echo: bool = True) -> dict:
    """The dashboard: each theme counted with its English words only."""
    out = {}
    for t, g in themes.items():
        words = list((g.get("terms") or {}).get("en") or [])
        hits, per = census(cli, pid, words, tickets, echo=echo)
        out[t] = dict(hits=sorted(hits), words=per, candidates=[])
    return out


def memory_read(cli: CLI, pid: str, themes: dict, tickets: dict, echo: bool = True, sweep: bool = True) -> dict:
    """Every word of every language in the taxonomy (`fact list --query` each). Then, for each ticket in a language
    the taxonomy has no words for, `search` with the ticket's own words: its nearest tickets that the taxonomy did
    match vote on its themes (2 of the 3 nearest). Search ranks by meaning, across languages."""
    read = {}
    for t, g in themes.items():
        terms = g.get("terms") or {}
        words = [w for ws in terms.values() for w in ws]
        hits, per = census(cli, pid, words, tickets, echo=echo)
        read[t] = dict(hits=sorted(hits), words=per, candidates=[], uncovered=sorted({r["lang"] for r in tickets.values()} - set(terms)))
    matched = {}
    for t, r in read.items():
        for i in r["hits"]:
            matched.setdefault(i, set()).add(t)
    covered = {l for g in themes.values() for l in (g.get("terms") or {})}
    knn = []
    if sweep:
        todo = sorted(k for k, r in tickets.items() if r["lang"] not in covered)
        for n, k in enumerate(todo):
            r = tickets[k]
            q = r.get("previous") or r["body"]
            res = search(cli, q, pid, top_k=6, echo=echo and n == 0)
            near = [h for h in res if h["record"] != k and h["record"] in matched][:3]
            votes = {}
            for h in near:
                for t in matched[h["record"]]:
                    votes[t] = votes.get(t, 0) + 1
            prop = sorted((t for t, v in votes.items() if v >= 2 and k not in read[t]["hits"]), key=list(themes).index)
            for t in prop:
                read[t]["candidates"].append(k)
            knn.append(dict(record=k, account=r["account"], lang=r["lang"], body=q,
                            near=[dict(record=h["record"], rank=h["rank"], lang=h["lang"], account=h["account"],
                                       themes=sorted(matched[h["record"]], key=list(themes).index)) for h in near],
                            votes=votes, proposed=prop, matched=sorted(t for t in themes if k in read[t]["hits"])))
        if echo and len(todo) > 1:
            note(f"… and {len(todo) - 1} more `search` the same way, one per ticket")
    for t in read:
        read[t]["knn"] = knn
    return read


def answer(story: dict, q: dict, read: dict, themes: dict, tickets: dict) -> dict:
    """One function for both reads: what the counts say, for one question."""
    lab = lambda t: themes[t]["label"]
    if q["kind"] == "top":
        counts = {t: len(read[t]["hits"]) for t, g in themes.items() if g.get("type", "request") == "request"}
        n = max(counts.values()) if counts else 0
        top = sorted(t for t, v in counts.items() if v == n)
        cand = {t: len(read[t]["candidates"]) for t in counts if read[t]["candidates"]}
        said = f"{' and '.join(map(lab, top))} ({n} tickets)"
        if cand:
            said += "; to review: " + ", ".join(f"+{v} {lab(t)}" for t, v in cand.items())
        return dict(key="+".join(top), said=said, short=f"{' + '.join(map(lab, top))} · {n}", counts=counts)
    r = read[q["theme"]]
    ids = r["hits"]
    accounts = sorted({tickets[i]["account"] for i in ids})
    cand = sorted({tickets[i]["account"] for i in r["candidates"]} - set(accounts))
    if q["kind"] == "churn":
        arr = sum(tickets[i]["arr"] for i in {min(i for i in ids if tickets[i]["account"] == a) for a in accounts})
        arr_c = sum(tickets[i]["arr"] for i in {min(i for i in r["candidates"] if tickets[i]["account"] == a) for a in cand})
        said = (f"{len(accounts)} account(s), {money(arr)} ARR: {', '.join(accounts) or 'none'}"
                + (f"; to review: {', '.join(cand)} (+{money(arr_c)})" if cand else ""))
        return dict(key=str(len(accounts)), said=said, short=f"{len(accounts)} · {money(arr)}" + (f" (+{len(cand)})" if cand else ""),
                    accounts=accounts, arr=arr, candidates=cand)
    said = f"{len(ids)} ticket(s)" + (f", +{len(r['candidates'])} to review" if r["candidates"] else "")
    langs = sorted({tickets[i]["lang"] for i in ids})
    return dict(key=str(len(ids)), said=f"{said} — in {', '.join(langs) or 'no language'}",
                short=f"{len(ids)}" + (f" (+{len(r['candidates'])})" if r["candidates"] else ""), langs=langs)


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    for name in ("feedback", "taxonomy"):
        p = story["projects"][name]
        proj = find_project(cli, p["key"], echo=False)
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]), "--description",
                           p["description"], scoped=True)
        else:
            note(f"“{p['name']}” exists from an earlier run (pass --reset to start over)")
        ids[name] = proj["id"]
        emit("text", f"    project  {p['name']:<34} {proj['id']}")
    emit("setup", "", projects=ids)
    return ids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, pid, echo=False):
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
        res = cli.run("fact", "add", "--project", pid, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = [f.get("id") for f in res.get("facts") or []]
        if len(got) != len(missing):
            die(f"`fact add` returned {len(got)} facts for {len(missing)} texts")
        for i, ((text, meta), fid) in enumerate(zip(missing, got)):
            cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta, ensure_ascii=False),
                    scoped=True, echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


REWRITE_KEYS = ("previous", "tagged_on", "tagged_by", "themes", "revised_on", "revised_by")


def restore_first_day(cli: CLI, pids: dict, story: dict) -> int:
    """Every run starts on the morning of the review: whatever an earlier run's step 6 rewrote is put back word for
    word from metadata.previous (and the taxonomy's terms from the data file)."""
    n = 0
    base = {g["theme"]: g for g in story["glossary"]}
    for pid in (pids["feedback"], pids["taxonomy"]):
        for f in list_facts(cli, pid, echo=False):
            m = f.get("metadata") or {}
            if not m.get("previous"):
                continue
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            if m.get("kind") == "theme" and m.get("theme") in base:
                meta["terms"] = base[m["theme"]]["terms"]
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata",
                    json.dumps(meta, ensure_ascii=False), scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} record(s); put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pids: dict, story: dict) -> tuple[dict, dict]:
    restored = restore_first_day(cli, pids, story)
    note("every ticket's feedback, word for word in the customer's language: one `fact add` for all of them, then "
         "`fact update --metadata` (account, country, language, plan, ARR, day, agent)")
    a = pin(cli, pids["feedback"], [(t["text"], ticket_meta(t)) for t in story["tickets"]])
    note("the feedback taxonomy, one fact per theme with the words that mark it in each language")
    b = pin(cli, pids["taxonomy"], [(theme_text(story, g, g["terms"]), theme_meta(story, g)) for g in story["glossary"]])
    new = sum(1 for g in list(a.values()) + list(b.values()) if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 5 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up")
        time.sleep(10)
    tickets = entries(list_facts(cli, pids["feedback"]))
    themes = ordered(entries(list_facts(cli, pids["taxonomy"])))
    langs = story["languages"]
    lines = ["", f"    {'language':<11} tickets  accounts"]
    for l in langs:
        rs = sorted((r for r in tickets.values() if r["lang"] == l), key=lambda r: r["record"])
        if rs:
            lines.append(f"    {langs[l]:<11} {len(rs):>4}     " + ", ".join(clip(r["account"], 20) for r in rs))
    lines.append("")
    for t, g in themes.items():
        cov = " ".join(sorted(g.get("terms") or {}))
        lines.append(f"    theme {g['label']:<40} words in: {cov}")
    miss = sorted({r["lang"] for r in tickets.values()} - {l for g in themes.values() for l in (g.get("terms") or {})})
    lines.append(f"\n    the taxonomy was written {story['glossary_by']['on']}; tickets arrive in "
                 f"{len({r['lang'] for r in tickets.values()})} languages, the taxonomy has words for "
                 f"{len({l for g in themes.values() for l in (g.get('terms') or {})})}"
                 + (f" (none for {', '.join(langs[m] for m in miss)})" if miss else ""))
    emit("text", "\n".join(lines))
    emit("intake", "", tickets=[public(r) for r in sorted(tickets.values(), key=lambda r: r["record"])],
         themes=[dict(theme=t, label=g["label"], terms=g.get("terms") or {}, body=g["body"]) for t, g in themes.items()],
         languages=langs, uncovered=miss, restored=restored)
    return tickets, themes


# --------------------------------------------------------------------------- step 4: the quick read

def ranking_lines(themes: dict, read: dict, tickets: dict) -> list[str]:
    rows = sorted(((t, g) for t, g in themes.items() if g.get("type") == "request"),
                  key=lambda tg: (-len(read[tg[0]]["hits"]), tg[1]["label"]))
    out = []
    for t, g in rows:
        r = read[t]
        langs = sorted({tickets[i]["lang"] for i in r["hits"]})
        c = f"  +{len(r['candidates'])} to review" if r["candidates"] else ""
        out.append(f"    {g['label']:<22} {len(r['hits']):>2}  {'█' * len(r['hits']):<8} {' '.join(langs)}{c}")
    return out


def quick_review(cli: CLI, pids: dict, story: dict, themes: dict, tickets: dict) -> dict:
    note(f"{story['product_lead']['name']} and {story['sales_lead']['name']} open the feedback dashboard: each theme "
         "counted with the taxonomy's English words, `fact list --query` (exact, every record)")
    read = quick_read(cli, pids["feedback"], themes, tickets)
    lines = ["", "  requests, by tickets that contain the English word:"] + ranking_lines(themes, read, tickets)
    answers = {}
    for q in story["questions"]:
        a = answer(story, q, read, themes, tickets)
        answers[q["id"]] = a
        lines.append(f"\n  {q['id']} {q['text']}\n  → quick read: {a['said']}")
    emit("text", "\n".join(lines))
    emit("quick", "", read=read, answers=answers, questions=story["questions"])
    return dict(read=read, answers=answers)


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pids: dict, story: dict, tickets: dict, quick: dict) -> dict:
    note("the memory read: the taxonomy as it is in memory (`fact list`), every word in every language "
         "(`fact list --query` each), then `search` — which ranks by meaning, across languages — for the languages "
         "the taxonomy has no words for")
    themes = ordered(entries(list_facts(cli, pids["taxonomy"])))
    read = memory_read(cli, pids["feedback"], themes, tickets)
    langs = [l for l in story["languages"] if any(r["lang"] == l for r in tickets.values())]
    lines = ["", f"  {'theme':<40} " + " ".join(f"{l:>3}" for l in langs) + "   all  (English words only)", "  " + "-" * 82]
    for t, g in themes.items():
        r = read[t]
        cells = []
        for l in langs:
            n = sum(1 for i in r["hits"] if tickets[i]["lang"] == l)
            c = sum(1 for i in r["candidates"] if tickets[i]["lang"] == l)
            cells.append(f"{(str(n) if n else '·') + ('?' if c else ''):>3}")
        lines.append(f"  {g['label']:<40} " + " ".join(cells) + f"   {len(r['hits']):>3}  ({len(quick['read'][t]['hits'])})")
    lines.append("  (? = proposed by search, in a language the taxonomy has no words for)")
    knn = next(iter(read.values()))["knn"] if read else []
    if knn:
        lines.append(f"\n  search by example — each ticket in a language without words, its own text as the query; the "
                     "3 nearest tickets the words did match vote:")
    for x in knn:
        lines.append(f"\n    {x['record']} {x['account']} ({x['lang']}) “{clip(x['body'], 70)}”")
        for h in x["near"]:
            lines.append(f"      #{h['rank']} {h['record']} {clip(h['account'], 18):<18} ({h['lang']})  "
                         + ", ".join(themes[t]["label"] for t in h["themes"]))
        got = [f"{themes[t]['label']} ({x['votes'][t]}/3)" for t in x["proposed"]]
        had = [themes[t]["label"] for t in x["matched"]]
        lines.append("      → " + ("proposes: " + "; ".join(got) if got else "proposes nothing")
                     + (f"   (already matched: {', '.join(had)})" if had else ""))
    emit("text", "\n".join(lines))
    rows = []
    lines = []
    for q in story["questions"]:
        a = answer(story, q, read, themes, tickets)
        qa = quick["answers"][q["id"]]
        agree = a["key"] == qa["key"]
        lines.append(f"\n  {q['id']} {q['text']}")
        lines.append(f"  → quick read:  {qa['said']}")
        lines.append(f"  → memory read: {a['said']}  {'✓ same' if agree else '✗ differs'}")
        rows.append(dict(question=q, quick=qa, memory=a, agree=agree))
    lines.append(f"\n  {'':<4} {'question':<48} {'quick read (English words)':<28} {'memory read (every language)':<30}")
    lines.append("  " + "-" * 118)
    for x in rows:
        lines.append(f"  {x['question']['id']:<4} {clip(x['question']['text'], 47):<48} {clip(x['quick']['short'], 27):<28} "
                     f"{clip(x['memory']['short'], 29):<30} {'✓ same' if x['agree'] else '✗ differs'}")
    wrong = sum(1 for x in rows if not x["agree"])
    lines.append(f"\n  → {wrong} of {len(rows)} answers change with the language of the words; every record and every match "
                 "is exact either way")
    emit("text", "\n".join(lines))
    emit("memory", "", read=read, rows=rows, languages=langs,
         themes=[dict(theme=t, label=g["label"]) for t, g in themes.items()])
    return dict(read=read, rows=rows, themes=themes)


# --------------------------------------------------------------------------- step 6: write it back

def stamp(labels: list[str], on: str) -> str:
    return f"(Feedback themes, tagged {on}: {'; '.join(labels)}.)"


def write_back(cli: CLI, pids: dict, story: dict, tickets: dict, mem: dict) -> dict:
    today, rv = story["today"], story["reviewer"]
    themes, read = mem["themes"], mem["read"]
    proposed = {(i, t) for t, r in read.items() for i in r["candidates"]}
    lines = [f"\n  {rv['name']} ({rv['role']}) reads the Portuguese tickets:"]
    confirmed = {}
    for rec, ts in story["review"].items():
        for t in ts:
            src = "search proposed it" if (rec, t) in proposed else ("the taxonomy matched it" if rec in read[t]["hits"]
                                                                      else "search missed it")
            confirmed.setdefault(rec, set()).add(t)
            lines.append(f"    {rec} {tickets[rec]['account']:<16} {themes[t]['label']:<40} ✓ ({src})")
    for rec, t in sorted(proposed):
        if t not in story["review"].get(rec, []):
            lines.append(f"    {rec} {tickets[rec]['account']:<16} {themes[t]['label']:<40} ✗ rejected (search proposed it)")
    emit("text", "\n".join(lines))
    # 1. the taxonomy learns Portuguese
    note("the taxonomy gets its Portuguese words (`fact update --text --metadata`; the old text goes to metadata.previous)")
    base = {g["theme"]: g for g in story["glossary"]}
    for i, (t, g) in enumerate(themes.items()):
        terms = dict(g.get("terms") or {})
        if rv["lang"] in terms:
            continue
        terms[rv["lang"]] = base[t][rv["lang"]]
        meta = {k: v for k, v in g.items() if k in ("key", "kind", "theme", "label", "type", "search", "by", "on")}
        meta.update(terms=terms, previous=g["body"], revised_on=today, revised_by=rv["name"])
        cli.run("fact", "update", g["fid"], "--project", pids["taxonomy"], "--text", theme_text(story, base[t], terms),
                "--metadata", json.dumps(meta, ensure_ascii=False), scoped=True, echo=i == 0)
    themes2 = ordered(entries(list_facts(cli, pids["taxonomy"], echo=False)))
    # 2. every non-English ticket says its themes in English
    word = themes["invoice-pdf"]["terms"]["en"][0]
    before = sorted(census(cli, pids["feedback"], [word], tickets)[0])
    note("every non-English ticket gets its themes appended in English (`fact update --text`, metadata.themes; the "
         "customer's words stay first, and in metadata.previous)")
    themed = {}
    for t, r in read.items():
        for i in r["hits"]:
            themed.setdefault(i, set()).add(t)
    for rec, ts in confirmed.items():
        themed.setdefault(rec, set()).update(ts)
    updates = []
    for rec, ts in sorted(themed.items()):
        r = tickets[rec]
        if r["lang"] == "en" or r.get("previous"):
            continue
        labels = [themes[t]["label"] for t in sorted(ts, key=list(themes).index)]
        meta = {k: r[k] for k in ("key", "kind", "record", "account", "country", "lang", "plan", "arr", "on", "agent") if k in r}
        meta.update(themes=sorted(ts), previous=r["body"], tagged_on=today, tagged_by=rv["name"] if r["lang"] == rv["lang"] else r["agent"])
        updates.append((r, f"{r['body']} {stamp(labels, today)}", meta))
    for i, (r, text, meta) in enumerate(updates):
        cli.run("fact", "update", r["fid"], "--project", pids["feedback"], "--text", text, "--metadata",
                json.dumps(meta, ensure_ascii=False), scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more ticket(s) the same way")
    tickets2 = entries(list_facts(cli, pids["feedback"]))
    lines = []
    for r, _, _ in [u for u in updates if u[0]["record"] in ("T-2113", "T-2131")]:
        lines.append(f"\n    {r['record']} was “{r['body']}”")
        lines.append(f"    {'':<{len(r['record'])}} now “{tickets2[r['record']]['body']}”")
    after_c = sorted(census(cli, pids["feedback"], [word], tickets2)[0])
    lines.append(f"\n  `fact list --query '{word}'` (exact substring, every record): before {len(before)} — {', '.join(before) or '—'}")
    lines.append(f"  {'':<{len(word) + 25}}after  {len(after_c)} — {', '.join(after_c) or '—'}")
    emit("text", "\n".join(lines))
    trace = []
    pick = next((u[0] for u in updates if u[0]["record"] == "T-2131"), updates[0][0] if updates else None)
    if pick:
        res = cli.run("fact", "trace", pick["fid"], "--project", pids["feedback"], scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {pick['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    note("the dashboard again: the same quick-read code, English words only, over the records as they are now")
    q2 = quick_read(cli, pids["feedback"], themes2, tickets2)
    m2 = memory_read(cli, pids["feedback"], themes2, tickets2, echo=False)
    lines = ["", "  requests, by tickets that contain the English word:"] + ranking_lines(themes2, q2, tickets2)
    after = []
    for q in story["questions"]:
        a, b = answer(story, q, q2, themes2, tickets2), answer(story, q, m2, themes2, tickets2)
        agree = a["key"] == b["key"]
        after.append(dict(question=q, quick=a, memory=b, agree=agree))
        lines.append(f"\n  {q['id']} {q['text']}\n    quick read:  {a['said']}\n    memory read: {b['said']}  "
                     f"{'✓ same' if agree else '✗ differs'}")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the dashboard now agrees on {agree} of {len(after)}: every ticket says its themes in the words the "
                 "company counts with, and the taxonomy covers Portuguese")
    emit("text", "\n".join(lines))
    out = dict(confirmed={k: sorted(v) for k, v in confirmed.items()}, proposed=sorted(f"{a}:{b}" for a, b in proposed),
               updated=[dict(record=r["record"], was=r["body"], now=tickets2[r["record"]]["body"]) for r, _, _ in updates],
               census=dict(query=word, before=before, after=after_c), trace=trace, after=after, agree=agree,
               ranking=ranking_lines(themes2, q2, tickets2), after_hits={t: r["hits"] for t, r in q2.items()})
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, themes

def need_projects(cli: CLI) -> dict:
    story = load_story()
    out = {}
    for name in ("feedback", "taxonomy"):
        proj = find_project(cli, story["projects"][name]["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        out[name] = proj["id"]
    return out


def english_words(themes: dict) -> list[str]:
    return [w for g in themes.values() for w in (g.get("terms") or {}).get("en") or []]


def ask_text(cli: CLI, pids: dict, text: str, echo: bool = True) -> dict:
    """Any question, in any language: what `search` returns, and which hits the dashboard's English words would count."""
    tickets = entries(list_facts(cli, pids["feedback"], echo=False))
    themes = ordered(entries(list_facts(cli, pids["taxonomy"], echo=False)))
    hits = search(cli, text, pids["feedback"], top_k=8, echo=echo)
    en = english_words(themes)
    rows = []
    for h in hits:
        r = tickets.get(h["record"]) or {}
        body = r.get("body") or h["body"]
        first = r.get("previous") or body
        words = [w for w in en if w.lower() in first.lower()]
        rows.append(dict(public(dict(r, rank=h["rank"])) or {}, english=words, stale=h["fid"] not in {x["fid"] for x in tickets.values()}))
    langs = sorted({x["lang"] for x in rows if x.get("lang")})
    return dict(question=text, hits=rows, langs=langs, english=sum(1 for x in rows if x["english"]))


def themes_view(cli: CLI, pids: dict) -> dict:
    """Every theme: tickets per language, counted with English words and with every language's words."""
    story = load_story()
    tickets = entries(list_facts(cli, pids["feedback"], echo=False))
    themes = ordered(entries(list_facts(cli, pids["taxonomy"])))
    m = memory_read(cli, pids["feedback"], themes, tickets, echo=False, sweep=False)
    # The English words are a subset of every language's: the dashboard's count comes from the same census.
    q = {t: dict(hits=sorted({i for w in (g.get("terms") or {}).get("en") or [] for i in m[t]["words"].get(w, [])}))
         for t, g in themes.items()}
    langs = [l for l in story["languages"] if any(r["lang"] == l for r in tickets.values())]
    rows = []
    for t, g in themes.items():
        per = {l: sum(1 for i in m[t]["hits"] if tickets[i]["lang"] == l) for l in langs}
        rows.append(dict(theme=t, label=g["label"], per=per, quick=len(q[t]["hits"]), memory=len(m[t]["hits"]),
                         covered=sorted(g.get("terms") or {}), revised=g.get("revised_on") or ""))
    return dict(langs=langs, rows=rows, tagged=sum(1 for r in tickets.values() if r.get("themes")))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    n_p = n_f = 0
    for name in ("feedback", "taxonomy"):
        proj = find_project(cli, story["projects"][name]["key"], echo=not quiet)
        if not proj:
            continue
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        for f in facts:
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_p, n_f = n_p + 1, n_f + len(facts)
    note(f"deleted {n_p} project(s) and their {n_f} facts" if n_p else "nothing to delete")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, f"Set up — ticket feedback and the feedback taxonomy, one project each")
    pids = setup(cli, story, reset)
    cli.pids = pids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"Intake — {len(story['tickets'])} tickets in {len({t['lang'] for t in story['tickets']})} languages, word for word")
    tickets, themes = intake(cli, pids, story)
    banner(4, TOTAL, "The quick read — the dashboard counts each theme with its English words")
    quick = quick_review(cli, pids, story, themes, tickets)
    banner(5, TOTAL, "The memory read — every language's words, and search for the languages without any")
    mem = memory_review(cli, pids, story, tickets, quick)
    banner(6, TOTAL, "Write it back — the taxonomy learns Portuguese, the tickets say their themes in English")
    wb = write_back(cli, pids, story, tickets, mem)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   quick={k: v["key"] for k, v in quick["answers"].items()},
                   memory={x["question"]["id"]: x["memory"]["key"] for x in mem["rows"]},
                   agree=[x["question"]["id"] for x in mem["rows"] if x["agree"]],
                   knn={x["record"]: [x["proposed"], [h["record"] for h in x["near"]]]
                        for x in next(iter(mem["read"].values()))["knn"]},
                   candidates={t: r["candidates"] for t, r in mem["read"].items() if r["candidates"]},
                   census=[len(wb["census"]["before"]), len(wb["census"]["after"])],
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "themes", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" = search the feedback in any language and see "
                         "which hits the English dashboard would count; themes = every theme per language, counted "
                         "with English words and with every language's; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    story = load_story()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Who wants invoices as PDF?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py themes`, `python3 demo.py ask \"Who wants invoices as PDF?\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    pids = need_projects(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — search the feedback in any language")
        r = ask_text(cli, pids, a.args[0])
        lines = [f"\n  “{r['question']}” — top {len(r['hits'])}, in {', '.join(r['langs'])}:"]
        for x in r["hits"]:
            mark = f"has a dashboard word: {', '.join(x['english'])}" if x["english"] else "no English dashboard word in it"
            lines.append(f"    #{x['rank']:<2} {x['record']} {x['lang']}  {clip(x['account'] or '', 18):<18} {clip(x['body'] or '', 60):<60}  {mark}")
        lines.append(f"\n  · search ranks by meaning across languages; {r['english']} of {len(r['hits'])} hits contain one of the "
                     "dashboard's English words in the customer's own words")
        emit("text", "\n".join(lines))
    elif a.command == "themes":
        banner(2, 2, "Themes — tickets per language, English words vs every language's")
        v = themes_view(cli, pids)
        lines = [f"\n  {'theme':<40} " + " ".join(f"{l:>3}" for l in v["langs"]) + "   every language   English words   words in",
                 "  " + "-" * 110]
        for x in v["rows"]:
            lines.append(f"  {x['label']:<40} " + " ".join(f"{x['per'][l] or '·':>3}" for l in v["langs"])
                         + f"   {x['memory']:>14}   {x['quick']:>13}   {' '.join(x['covered'])}"
                         + (f"  (revised {x['revised']})" if x["revised"] else ""))
        lines.append(f"\n  · {v['tagged']} ticket(s) carry their themes in English (step 6)" if v["tagged"] else
                     "\n  · no ticket carries its themes in English yet")
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

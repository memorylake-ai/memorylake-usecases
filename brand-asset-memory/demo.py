#!/usr/bin/env python3
"""
Brand asset memory for design teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Fernway Coffee Roasters keeps its brand in a DAM. What comes out of it is a
folder of images called IMG_2031.png, IMG_2052.png, … — the names say nothing
about what is in them, so every new designer asks the brand lead, Dana Reyes,
"which logo do I use?" and gets a file pasted into chat.

The demo imports the six exported images into one MemoryLake project.
MemoryLake looks at each picture: it writes down what is in it, reads the text
on it, and lists the questions the picture answers.  Dana's sign-off meeting
(images sent in the chat as IMAGE blocks) and her brand rules (pinned facts)
go into the same memory.  Then Ivo Marsh, a freelancer starting on the winter
packaging, asks his questions and gets back the right picture for each one —
picked by what is in it, not by its name — plus the original file, ready to
use.  Finally the retired 2019 wordmark is removed from memory, and the same
question no longer returns it.

Everything the script does is a plain CLI command, echoed as it runs, so you
can copy any line into your own shell.  The companion web app (web/server.py)
drives the same functions and streams the same events to a browser.

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
ASSETS = DATA / "assets"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-bam"  # memorylake-usecases / brand-asset-memory; keeps demo ids apart from yours

PEOPLE = {
    "dana": dict(display="Dana Reyes", type="HUMAN", tags="brand,fernway",
                 role="Brand lead, Fernway Coffee Roasters"),
    "assistant": dict(display="Canvas", type="ASSISTANT", tags="ai,design-assistant",
                      role="Design assistant (AI) used by the Fernway design team"),
}
DESIGNER = dict(display="Ivo Marsh", role="Freelance designer, winter packaging")
PROJECT = dict(
    custom_id=f"{PREFIX}-fernway-brand",
    name="Fernway Coffee Roasters — brand asset memory",
    description="Brand images exported from the DAM, the brand review, and the brand rules. Demo data.",
)
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# The new designer's questions. `expect` is the file the brand lead would have sent; the demo
# checks that MemoryLake ranks it first. File names never appear in the questions. Ranking is
# sensitive to wording: describe what is in the picture ("a fern in a green circle"), the way you
# would to a colleague. Measured 5/5 at rank 1 for each of these phrasings (see README).
QUESTIONS = [
    dict(heading="The logo with the fern in a green circle: which file is it?",
         query="Fernway coffee roasters logo with a fern in a green circle", expect="IMG_2031.png"),
    dict(heading="What must I never do with the logo?",
         query="examples of incorrect logo usage to avoid", expect="IMG_2052.png"),
    dict(heading="What does the approved seasonal bag look like?",
         query="approved seasonal coffee bag packaging design", expect="IMG_2088.jpg"),
    dict(heading="Is there a template for announcing a new roast on social media?",
         query="template for announcing a new coffee on social media", expect="IMG_2093.png"),
    dict(heading="Which colors, with hex codes, and where may each one go?",
         query="brand color palette hex codes and where each color is used", expect="IMG_2047.png", rules=True),
]
# Step 7: the asset that leaves the brand.
RETIRE = dict(file="IMG_1960.png", heading="Is there another Fernway wordmark?",
              query="the Fernway & Co. serif wordmark")
# Step 4: one wide query that brings back every image with what MemoryLake saw in it.
SURVEY_QUERY = "Fernway brand image"

# --------------------------------------------------------------------------- output sink
#
# Everything the demo says goes through `emit`.  The CLI runner prints; the web app
# replaces EMIT with a function that streams the same events to the browser.

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
    # "call", "turn", "facts", "brief" carry structured data for the web app; the text
    # versions of the same information are emitted alongside them.


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
    """Thin wrapper: runs `memorylake …`, echoes the command, parses the JSON reply."""

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary = binary
        self.env = env
        self.show_json = show_json
        self.workspace: str | None = None
        self.secret: str | None = None

    def _echo(self, args: list[str]) -> None:
        shown = list(args)
        if self.secret:
            shown = [a.replace(self.secret, "sk-…") for a in shown]
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, scoped: bool = False, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args]
        if scoped and self.workspace:
            argv += ["--workspace", self.workspace]
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    # Read-only subcommands are safe to repeat when the network hiccups.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

    def run(self, *args: str, scoped: bool = False, echo: bool = True):
        """Run and return the parsed JSON (or raw text when the reply is not JSON).

        A read-only command that fails on a transient network error (a dropped TLS handshake,
        a reset connection, a gateway timeout) is retried a few times with a short backoff
        instead of aborting the whole demo. Writes are never repeated here."""
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup ({err.strip().splitlines()[-1] if err.strip() else 'connect'}); retry {attempt}/4 in {2 * attempt}s")
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
        """Like run(), but returns None when the server says the thing does not exist."""
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
    """Log in (isolated profile when a key is given) and pick a workspace."""
    binary = find_binary()
    env = dict(os.environ)
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)

    if api_key:
        # Keep the demo's credentials in its own config dir so your ~/.memorylake is untouched.
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

    # Workspace: explicit → env var → the one remembered by `ws use` → the first one listed.
    ws = (workspace or env.get("MEMORYLAKE_WORKSPACE", "")).strip()
    ws_name = ""
    if not ws:
        rc, out, _ = cli.raw("ws", "current")
        m = re.search(r"\b(ws-[0-9a-f]+)\b", out) if rc == 0 else None
        ws = m.group(1) if m else ""
    if not ws:
        listing = cli.run("ws", "list", "--page-size", "1")
        items = listing.get("items") or []
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    return cli




# --------------------------------------------------------------------------- step 2: setup

def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type.
    # Pass it whenever the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    if not cli.has_actor_type and actor_type != "HUMAN":  # type: ignore[attr-defined]
        note(f"this CLI cannot set an actor type, so this {actor_type} actor is created untyped (server default HUMAN)")
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def ensure_actor(cli: CLI, key: str) -> str:
    p = PEOPLE[key]
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", p["display"],
                        *actor_type_args(cli, p["type"]), "--tags", p["tags"], "--description", p["role"])
        note(f"created actor {p['display']} → {actor['id']}")
    else:
        note(f"actor {p['display']} already exists → {actor['id']}")
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    emit("actor", "", key=key, id=actor["id"], display=p["display"], role=p["role"])
    return actor["id"]


def load_review() -> dict:
    return json.loads((DATA / "brand-review.json").read_text(encoding="utf-8"))


def asset_files() -> list[Path]:
    return sorted(p for p in ASSETS.iterdir() if p.suffix.lower() in MIME)


def delete_conversation(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    conv = cli.try_run("conv", "get", load_review()["custom_id"], "--by-custom-id", scoped=True)
    if conv is None:
        return 0
    cli.run("conv", "delete", conv["id"], scoped=True)
    return 1


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI, reset: bool) -> str:
    proj = find_project(cli)
    if proj is not None and reset:
        note(f"--reset: deleting the demo conversation and project {proj['id']} (documents go with it)")
        delete_conversation(cli)
        cli.run("proj", "delete", proj["id"], scoped=True)
        proj = None
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=True)
        return proj["id"]
    note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", id=proj["id"], name=proj["name"], fresh=False)
    return proj["id"]


# --------------------------------------------------------------------------- step 3: the DAM export → documents

def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    docs, token = [], None
    while True:
        args = ["proj", "doc", "list", "--project", project_id]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        docs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return docs


def upload_assets(cli: CLI) -> dict[str, dict]:
    """Upload every image to the Library. Returns {file name: {item_id, uri}}."""
    uploaded = {}
    for path in asset_files():
        # `overwrite` keeps the same Library item id on re-runs instead of creating name_1, name_2, …
        item = cli.run("lib", "upload", str(path), "--on-conflict", "overwrite")
        uploaded[path.name] = dict(item_id=item["item_id"], uri=item["uri"])
        note(f"uploaded {item['name']}")
        emit("uploaded", "", name=item["name"], item_id=item["item_id"])
    return uploaded


def ingest_assets(cli: CLI, project_id: str) -> dict[str, dict]:
    uploaded = upload_assets(cli)
    # --wait blocks until the server has processed every image.
    t0 = time.time()
    result = cli.run("proj", "doc", "import", "--project", project_id,
                     *[u["item_id"] for u in uploaded.values()], "--wait", scoped=True)
    note(f"imported: {result.get('success_count', 0)} new, {result.get('duplicate_count', 0)} already in project, "
         f"{result.get('failure_count', 0)} failed ({int(time.time() - t0)}s)")
    emit("imported", "", **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})
    docs = sorted(list_documents(cli, project_id), key=lambda d: d.get("name") or "")
    lines = [f"\n  {len(docs)} brand images in memory — this is all a file name tells you:\n"]
    lines += [f"   - {d.get('name'):<16} {d.get('status')}" for d in docs]
    emit("text", "\n".join(lines))
    emit("documents", "", documents=[dict(id=d.get("id"), name=d.get("name"), status=d.get("status")) for d in docs])
    return uploaded


# --------------------------------------------------------------------------- step 4: what MemoryLake saw

def describe(hit: dict) -> dict:
    """One document hit → what MemoryLake saw in the picture."""
    name = hit.get("document_name") or hit.get("file_name") or ""
    summary = (hit.get("document_summary") or "").strip()
    if summary.startswith(name):  # summaries start with the file name
        summary = summary[len(name):].strip()
    questions, text = [], []
    for item in hit.get("items") or []:
        if item.get("type") == "figure":
            questions += item.get("prequestion_list") or []
        elif item.get("type") == "paragraph":
            text += [c.get("text") or "" for c in (item.get("highlight") or {}).get("chunks") or []]
    # The figure item also carries `persist_path`, a signed download URL: never printed here.
    return dict(id=hit.get("document_id"), name=name, kind=hit.get("source_type"), summary=summary,
                questions=questions, text=" / ".join(t.replace("\n", " ").strip() for t in text if t.strip()))


def search_assets(cli: CLI, project_id: str, query: str, top_k: int, echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--projects", project_id, "--types", "document", "--top-k", str(top_k),
                  scoped=True, echo=echo)
    return [describe(d) for d in res.get("documents") or []]


def clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def show_seen(cli: CLI, project_id: str) -> list[dict]:
    hits = search_assets(cli, project_id, SURVEY_QUERY, top_k=10)
    hits.sort(key=lambda h: h["name"])
    lines = ["\n  ▶ None of these names says what is in the picture. MemoryLake looked at each one:\n"]
    for h in hits:
        lines.append(f"   {h['name']}  ({h['kind']})")
        lines.append(f"     saw:        {clip(h['summary'], 150)}")
        if h["text"]:
            lines.append(f"     read on it: “{clip(h['text'], 90)}”")
        if h["questions"]:
            lines.append(f"     answers:    {h['questions'][0]}")
            for q in h["questions"][1:3]:
                lines.append(f"                 {q}")
        lines.append("")
    emit("text", "\n".join(lines).rstrip())
    emit("seen", "", assets=hits)
    return hits


# --------------------------------------------------------------------------- step 5: the brand review

def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> bool:
    """Messages are stored instantly; the facts are extracted in the background. Poll until done."""
    t0 = last_report = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first)
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            note("cook-status probe failed on the network; will try again on the next poll")
            status = {}
        first = False
        if status.get("cook_finished"):
            note(f"memory ready in {int(time.time() - t0)}s")
            emit("cooked", "", id=conv_id, seconds=int(time.time() - t0))
            return True
        if time.time() - last_report >= 30:
            emit("progress", f"  … still being processed ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last_report = time.time()
        time.sleep(5)
    note(f"gave up after {timeout}s; still processing server-side")
    return False


def speaker_line(key: str, text: str) -> str:
    p = PEOPLE[key]
    return f"{p['display']} ({p['role']}): {text}"


def ingest_review(cli: CLI, project_id: str, actors: dict[str, str], uploaded: dict[str, dict]) -> str:
    review = load_review()
    conv = cli.try_run("conv", "get", review["custom_id"], "--by-custom-id", scoped=True)
    done = 0
    if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
        # Left over from a deleted project (deleting a project does not delete its conversations).
        note("the review belongs to a deleted project; recreating it")
        cli.run("conv", "delete", conv["id"], scoped=True)
        conv = None
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", review["custom_id"], "--project", project_id,
                       "--actors", f"{actors['dana']},{actors['assistant']}", "--kind", "DIRECT",
                       "--name", review["name"], "--metadata", "kind=brand-review", scoped=True)
    else:
        done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
        note(f"review exists with {done} message(s) → {conv['id']}")
    emit("review", "", id=conv["id"], name=review["name"], date=review["date"], turns=len(review["turns"]), done=done)
    start = datetime.fromisoformat(review["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    parent = conv.get("current_message_id")
    for i, (speaker, text, image) in enumerate(review["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                "--custom-id", f"turn-{i:02d}", "--timestamp", ts]
        if image:
            # The picture travels with the message as an IMAGE block pointing at the Library item.
            blocks = [{"block_type": "TEXT", "text": speaker_line(speaker, text)},
                      {"block_type": "IMAGE", "uri": uploaded[image]["uri"],
                       "mime_type": MIME[Path(image).suffix.lower()]}]
            args += ["--content-json", json.dumps(blocks, ensure_ascii=False)]
        else:
            args += ["--text", speaker_line(speaker, text)]
        if parent:
            args += ["--parent", parent]
        parent = cli.run(*args, scoped=True)["id"]
        emit("turn", "", index=i, speaker=speaker, display=PEOPLE[speaker]["display"], line=text, image=image)
    if done < len(review["turns"]):
        note(f"{len(review['turns']) - done} message(s) appended; waiting for MemoryLake to extract the facts")
        wait_for_memory(cli, conv["id"])
        # Dana's rules, pinned verbatim in the run that stores the review (on a re-run they are
        # already there, possibly reworded by later extraction, so never re-pin by text).
        cli.run("fact", "add", "--project", project_id, *review["pinned"], scoped=True)
        note(f"{len(review['pinned'])} brand rule(s) pinned as facts")
    emit("pinned", "", notes=review["pinned"])
    return conv["id"]


def replay_images(cli: CLI, conv_id: str) -> list[dict]:
    """Read the review back: the IMAGE blocks are still there, and each uri resolves to its file."""
    msgs = cli.run("conv", "msg", "list", conv_id, "--page-size", "50").get("items") or []
    msgs.sort(key=lambda m: m.get("sequence_no") or 0)
    shown, lines = [], ["\n  The review, read back from memory (IMAGE blocks resolved to their Library files):\n"]
    for m in msgs:
        blocks = m.get("content") or []
        text = next((b.get("text") for b in blocks if b.get("block_type") == "TEXT"), "") or ""
        images = [b for b in blocks if b.get("block_type") == "IMAGE"]
        lines.append(f"   {(m.get('timestamp') or '')[:16].replace('T', ' ')}  {clip(text, 96)}")
        for b in images:
            item = cli.run("lib", "get", b["uri"].rsplit("/", 1)[-1])
            lines.append(f"       [IMAGE {b.get('mime_type')}] → {item.get('name')}")
            shown.append(dict(message=m.get("id"), name=item.get("name"), mime=b.get("mime_type")))
    emit("text", "\n".join(lines))
    emit("replay", "", images=shown, messages=len(msgs))
    return shown


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", flag, scope_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def show_facts(cli: CLI, project_id: str, dana_id: str) -> list[dict]:
    # A DIRECT conversation attached to a project leaves its facts on the project, on the actor,
    # or on both — read both scopes.
    pinned = set(load_review()["pinned"])
    seen, facts = set(), []
    for flag, sid, scope in (("--projects", project_id, "project"), ("--actors", dana_id, "Dana")):
        for f in list_facts(cli, flag, sid):
            if f["id"] not in seen:
                seen.add(f["id"])
                facts.append(dict(id=f["id"], fact=f.get("fact") or "", scope=scope,
                                  pinned=(f.get("fact") or "") in pinned))
    n_pinned = sum(f["pinned"] for f in facts)
    lines = [f"\n  {len(facts)} facts: {n_pinned} brand rules pinned verbatim, "
             f"{len(facts) - n_pinned} written by MemoryLake from the review.\n"]
    for f in sorted(facts, key=lambda f: (not f["pinned"], f["fact"])):
        lines.append(f"   {'pinned ' if f['pinned'] else 'learned'}  {f['fact']}")
    lines.append("\n  ▶ The images in the chat are stored and come back on replay, but facts are only written from"
                 "\n    the words. What a picture shows is read when the file is imported as a document (step 3–4).")
    emit("text", "\n".join(lines))
    emit("facts", "", facts=facts)
    return facts


# --------------------------------------------------------------------------- step 6: the new designer asks

RULE_WORDS = re.compile(r"#[0-9A-F]{6}|\b(green|red|oat|brown|colou?r|palette|accent|background)\b", re.I)


def ask(cli: CLI, project_id: str, q: dict, top_k: int) -> dict:
    hits = search_assets(cli, project_id, q["query"], top_k)
    answer = dict(heading=q["heading"], query=q["query"], expect=q["expect"], hits=hits, rules=[], left_out=0)
    if q.get("rules"):
        res = cli.run("search", q["query"], "--projects", project_id, "--types", "fact", "--top-k", "5", scoped=True)
        facts = res.get("facts") or []
        # search always returns top-k; keep the ones about colour and say how many were left out.
        answer["rules"] = [f.get("fact") for f in facts if RULE_WORDS.search(f.get("fact") or "")]
        answer["left_out"] = len(facts) - len(answer["rules"])
    top = hits[0] if hits else None
    answer["top"] = top["name"] if top else None
    answer["ok"] = bool(top and top["name"] == q["expect"])
    return answer


def fetch_original(cli: CLI, project_id: str, hit: dict) -> dict:
    """Download the original file behind a hit — the asset itself, not a description of it."""
    target = OUT / "assets" / hit["name"]
    target.parent.mkdir(parents=True, exist_ok=True)
    cli.run("proj", "doc", "download", hit["id"], "--project", project_id, "--output", str(target), "--force",
            scoped=True)
    original = ASSETS / hit["name"]
    same = target.exists() and original.exists() and original.read_bytes() == target.read_bytes()
    return dict(path=str(target.relative_to(HERE)), size=target.stat().st_size if target.exists() else 0,
                identical=same)


def show_answer(a: dict) -> None:
    lines = [f"\n  Q ({DESIGNER['display']}): {a['heading']}"]
    for i, h in enumerate(a["hits"][:3], start=1):
        lines.append(f"     #{i} {h['name']:<14} {clip(h['summary'], 104)}")
    if a["hits"][3:]:
        lines.append(f"     ({len(a['hits']) - 3} lower-ranked image(s) not shown)")
    for r in a["rules"]:
        lines.append(f"     rule  {r}")
    if a["left_out"]:
        lines.append(f"     ({a['left_out']} other fact hit(s) left out: not about colour)")
    if a["ok"]:
        lines.append(f"     ✓ rank 1 is {a['top']} — the file the brand lead would have sent")
    else:
        lines.append(f"     ✗ rank 1 is {a['top']}; the brand lead would have sent {a['expect']}")
    if a.get("download"):
        d = a["download"]
        lines.append(f"     ↓ {d['path']} ({d['size']:,} bytes"
                     + (", byte-for-byte the file that was exported)" if d["identical"] else ")"))
    emit("text", "\n".join(lines))


def render_brief(answers: list[dict]) -> str:
    lines = [f"# Fernway winter packaging — brand brief for {DESIGNER['display']}",
             "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from MemoryLake brand memory "
             f"(project `{PROJECT['custom_id']}`). Each image is the top search hit for the question, downloaded "
             f"from memory; the description under it is what MemoryLake saw in the picture._",
             ""]
    for a in answers:
        lines.append(f"## {a['heading']}")
        if a["hits"]:
            top = a["hits"][0]
            if a.get("download"):
                lines.append(f"![{top['name']}]({Path(a['download']['path']).relative_to('out').as_posix()})")
                lines.append("")
            lines.append(f"`{top['name']}` — {top['summary']}")
        for r in a["rules"]:
            lines.append(f"- {r}")
        lines.append("")
    return "\n".join(lines)


def build_brief(cli: CLI, project_id: str, top_k: int) -> list[dict]:
    answers = []
    for q in QUESTIONS:
        a = ask(cli, project_id, q, top_k)
        if a["hits"]:
            a["download"] = fetch_original(cli, project_id, a["hits"][0])
        show_answer(a)
        emit("answer", "", **a)
        answers.append(a)
    ok = sum(a["ok"] for a in answers)
    note(f"{ok}/{len(answers)} questions answered with the expected image at rank 1")
    OUT.mkdir(exist_ok=True)
    (OUT / "brand-brief.md").write_text(render_brief(answers), encoding="utf-8")
    note(f"brief with the images embedded written to {(OUT / 'brand-brief.md').relative_to(HERE)}")
    emit("brief", "", path="out/brand-brief.md", ok=ok, total=len(answers))
    return answers


# --------------------------------------------------------------------------- step 7: retire an asset

def rank_of(hits: list[dict], name: str) -> int | None:
    return next((i for i, h in enumerate(hits, start=1) if h["name"] == name), None)


def retire(cli: CLI, project_id: str, top_k: int) -> dict:
    name = RETIRE["file"]
    before = search_assets(cli, project_id, RETIRE["query"], top_k)
    r = rank_of(before, name)
    hit = next((h for h in before if h["name"] == name), None)
    lines = [f"\n  Q: {RETIRE['heading']}",
             f"     before: {name} is " + (f"rank {r} of {len(before)} — “{clip(hit['summary'], 90)}”" if r else "not returned")]
    emit("text", "\n".join(lines))
    doc = next((d for d in list_documents(cli, project_id, echo=False) if d.get("name") == name), None)
    if doc is None:
        note(f"{name} is not in the project; nothing to retire")
        return dict(file=name, before=r, after=None, deleted=False)
    # Removes the document and everything MemoryLake derived from it. The Library file stays.
    cli.run("proj", "doc", "delete", doc["id"], "--project", project_id, scoped=True)
    after = search_assets(cli, project_id, RETIRE["query"], top_k)
    r2 = rank_of(after, name)
    emit("text", f"     after:  {name} is " + (f"still rank {r2}" if r2 else
                                                f"not returned ({len(after)} image(s) came back, none of them it)")
         + ("\n  ▶ Retired means gone: no tool that reads this memory can hand the old wordmark to anyone again."
            if r2 is None else ""))
    res = dict(file=name, before=r, after=r2, deleted=True, top_after=after[0]["name"] if after else None)
    emit("retired", "", **res)
    return res


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversation(cli)
    note(f"deleted {n} conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted project (its documents and facts went with it)")
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors")
    # Library files are not owned by the project; remove the ones we uploaded, by name.
    names = {p.name for p in asset_files()}
    token, removed = None, 0
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None)
        for item in page.get("items") or []:
            if item.get("name") in names:
                cli.run("lib", "delete", item.get("item_id") or item.get("id"))
                removed += 1
        token = page.get("continuation_token")
        if not token:
            break
    note(f"deleted {removed} uploaded image(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False, top_k: int = 6) -> list[dict]:
    """Steps 2–7. Step 1, connecting, is the caller's job."""
    banner(2, TOTAL, "Set up — the brand lead, the design assistant, one project for the brand")
    actors = {key: ensure_actor(cli, key) for key in PEOPLE}
    project_id = ensure_project(cli, reset)

    banner(3, TOTAL, "The DAM export — six images with names that say nothing")
    uploaded = ingest_assets(cli, project_id)

    banner(4, TOTAL, "What MemoryLake saw in each picture")
    show_seen(cli, project_id)

    banner(5, TOTAL, "The brand review — images sent in the chat, rules pinned")
    conv_id = ingest_review(cli, project_id, actors, uploaded)
    replay_images(cli, conv_id)
    show_facts(cli, project_id, actors["dana"])

    banner(6, TOTAL, f"{DESIGNER['display']} starts on winter packaging — and asks the memory")
    answers = build_brief(cli, project_id, top_k)

    banner(7, TOTAL, "Retire the 2019 wordmark — and it stops coming back")
    retire(cli, project_id, top_k)
    return answers


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "seen", "cleanup"],
                    help="run = full demo (default); ask = only the designer's questions again; "
                         "seen = only what MemoryLake saw in each image; "
                         "cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo project before importing")
    ap.add_argument("--top-k", type=int, default=6, help="images returned per question (default 6)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
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

    if args.command in ("ask", "seen"):
        proj = find_project(cli)
        if proj is None:
            die("the demo project does not exist yet; run `python3 demo.py` first")
        if args.command == "seen":
            banner(2, 2, "What MemoryLake saw in each picture")
            show_seen(cli, proj["id"])
        else:
            banner(2, 2, f"{DESIGNER['display']}'s questions, answered from brand memory")
            build_brief(cli, proj["id"], args.top_k)
        return

    run_pipeline(cli, reset=args.reset, top_k=args.top_k)
    print("\nDone. Open out/brand-brief.md, re-run `python3 demo.py ask` any time, "
          "or `python3 demo.py cleanup` to remove the demo data.")


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

#!/usr/bin/env python3
"""
Companion web app for the onboarding-memory demo.

    python3 web/server.py            # opens http://127.0.0.1:8765

It runs the very same pipeline as demo.py (it imports it) and streams every
event — each `memorylake` command, each uploaded file, each chat message, the
policy answers with their version verdicts, the audit tables, the day-one stage
change and the brief — to the browser over Server-Sent Events.  Standard library only.

The API key you paste into the page is kept in this process's memory and in the
demo's isolated CLI profile (./.memorylake-demo/), nowhere else.  The server
binds to localhost only.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import demo  # noqa: E402

STATIC = HERE / "static"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8"}


class Bus:
    """Append-only event log with a condition variable, replayable from any id."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.cond = threading.Condition()

    def push(self, kind: str, text: str, data: dict) -> dict:
        with self.cond:
            ev = {"id": len(self.events) + 1, "kind": kind, "text": text, "ts": round(time.time(), 3), "data": data}
            self.events.append(ev)
            self.cond.notify_all()
            return ev

    def wait(self, after_id: int, timeout: float) -> list[dict]:
        with self.cond:
            self.cond.wait_for(lambda: len(self.events) > after_id, timeout)
            return self.events[after_id:]


class App:
    def __init__(self) -> None:
        self.bus = Bus()
        self.cli: demo.CLI | None = None
        self.lock = threading.Lock()
        self.job: str | None = None
        self.status = "idle"            # idle | running | done | error
        self.error: str | None = None
        self.projects: dict[str, dict] = {}   # policies|chats -> {id, name}
        self.memory: list | None = None
        self.brief: dict | None = None
        demo.EMIT = self.sink

    # ---- the sink demo.py talks to
    def sink(self, kind: str, text: str, data: dict) -> None:
        if kind == "project":
            self.projects[data["key"]] = {"id": data["id"], "name": data["name"]}
        elif kind == "memory":
            self.memory = data["scopes"]
        elif kind == "brief":
            self.brief = data
        elif kind == "cmd":
            print("$ " + text, flush=True)
        self.bus.push(kind, text, data)

    # ---- state for the page
    def state(self) -> dict:
        cli = self.cli
        return {
            "connected": cli is not None,
            "team": getattr(cli, "team", None) and {"name": cli.team.get("name"), "role": cli.team.get("caller_role")},
            "workspace": cli and {"id": cli.workspace, "name": getattr(cli, "workspace_name", "") or cli.workspace},
            "status": self.status, "job": self.job, "error": self.error,
            "projects": self.projects, "has_memory": bool(self.memory), "has_brief": bool(self.brief),
            "last_event_id": len(self.bus.events),
        }

    # ---- jobs (one at a time)
    def start(self, name: str, fn) -> None:
        with self.lock:
            if self.status == "running":
                raise RuntimeError(f"a job is already running ({self.job})")
            self.status, self.job, self.error = "running", name, None

        def body():
            try:
                fn()
                self.status = "done"
                self.bus.push("done", name, {"job": name})
            except Exception as e:  # noqa: BLE001 — surface everything to the page
                self.status, self.error = "error", str(e)
                self.bus.push("error", str(e), {"job": name})

        threading.Thread(target=body, name=name, daemon=True).start()

    def connect(self, api_key: str, base_url: str, workspace: str) -> dict:
        with self.lock:
            if self.status == "running":
                raise RuntimeError("wait for the running job to finish")
        self.bus.push("step", f"Step 1/{demo.TOTAL}  Connect — API key, team, workspace",
                      {"index": 1, "total": demo.TOTAL, "title": "Connect"})
        self.cli = demo.connect(api_key=api_key or None, base_url=base_url or None, workspace=workspace or None)
        for key in demo.PROJECTS:
            proj = demo.find_project(self.cli, key)
            if proj:
                self.projects[key] = {"id": proj["id"], "name": proj["name"]}
                self.bus.push("project", "", {"key": key, "id": proj["id"], "name": proj["name"], "existing": True})
        self.bus.push("connected", "", self.state())
        return self.state()

    def run(self, reset: bool) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli
        if reset:
            self.memory = self.brief = None
        self.start("run", lambda: demo.run_pipeline(cli, reset=reset))

    def cleanup(self) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli

        def body():
            self.bus.push("step", "Cleanup — remove everything the demo created", {"index": 0, "total": 0, "title": "Cleanup"})
            demo.cleanup(cli)
            self.projects = {}
            self.memory = self.brief = None

        self.start("cleanup", body)

    def _projects(self) -> dict[str, str]:
        if not self.cli:
            raise RuntimeError("connect first")
        if len(self.projects) < len(demo.PROJECTS):
            ids = demo.find_projects(self.cli)  # raises with a hint when the demo has not run yet
            self.projects = {k: {"id": i, "name": demo.PROJECTS[k]["name"]} for k, i in ids.items()}
        return {k: p["id"] for k, p in self.projects.items()}

    def ask(self, query: str, hire: str, as_of: str) -> dict:
        """A free question from one new hire, judged like step 6: version in force, stale ones marked."""
        if hire not in demo.HIRES or not query:
            raise ValueError("pick a new hire and type a question")
        as_of = as_of or demo.today()
        demo.date.fromisoformat(as_of)  # ValueError on a bad date
        projects = self._projects()
        hires = demo.find_actor_ids(self.cli, demo.HIRES)
        known = demo.catalog(self.cli, projects["policies"], echo=False)
        about = {f["id"]: f.get("fact") for f in demo.list_facts(self.cli, "actors", hires[hire], echo=False)}
        docs = {d.get("name") for d in demo.list_documents(self.cli, projects["policies"], echo=False)}
        # Which policy is the question about? The one whose versions the search ranks first.
        res = demo.search(self.cli, query, [projects["policies"]], [hires[hire]])
        top = next((s["policy"] for f in res.get("facts") or [] if (s := demo.stamp(f.get("fact")))), None)
        if top is None:
            return {"question": query, "hire": hire, "as_of": as_of, "policy": None, "rows": [], "docs": [],
                    "about": [], "answer": None, "left_out": len(res.get("facts") or [])}
        question = dict(policy=top, q=query, guard=r"(?!)")  # free questions: personal facts are not filtered by topic, so leave them out
        return demo.answer(self.cli, projects, hire, hires[hire], question, known, about, docs, as_of=as_of)

    def audit(self, dates: list[str]) -> list:
        for d in dates:
            demo.date.fromisoformat(d)
        return demo.audit(self.cli, self._projects(), dates)

    def refresh_memory(self) -> list:
        demo.show_memory(self.cli, self._projects(), demo.find_actor_ids(self.cli, demo.HIRES))
        return self.memory or []


APP = App()


def data_bundle() -> dict:
    spec = demo.load_policies()
    return {
        "people": {k: dict(key=k, **v) for k, v in demo.PEOPLE.items()},
        "hires": {k: dict(key=k, **v) for k, v in demo.HIRES.items()},
        "projects": {k: dict(key=k, **spec_) for k, spec_ in demo.PROJECTS.items()},
        "library_root": demo.LIBRARY_ROOT,
        "files": [dict(path=p.relative_to(demo.LIBRARY_DIR).as_posix(), size=p.stat().st_size,
                       text=p.read_text(encoding="utf-8") if p.suffix == ".md" else None)
                  for p in demo.library_files()],
        "policies": [dict(id=p["id"], title=p["title"], versions=[demo.stamp(v) for v in p["versions"]])
                     for p in spec["policies"]],
        "flows": [demo.FLOW.match(f).groupdict() for f in spec["flows"]],
        "sessions": demo.load_sessions(),
        "questions": demo.QUESTIONS,
        "first_day_q": demo.FIRST_DAY_Q,
        "audit_date": demo.AUDIT_DATE,
        "today": demo.today(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "onboarding-memory/1.0"

    def log_message(self, fmt, *args):  # keep the console for the CLI commands
        if "/api/stream" in (args[0] if args else "") or self.path.startswith("/static"):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def _file(self, path: Path):
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- routes
    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if url.path.startswith("/static/"):
            return self._file(STATIC / url.path[len("/static/"):])
        if url.path.startswith("/library/"):  # the files as committed, to open next to the tree
            target = (demo.LIBRARY_DIR / url.path[len("/library/"):]).resolve()
            if demo.LIBRARY_DIR.resolve() not in target.parents:
                return self._json({"error": "not found"}, 404)
            return self._file(target)
        if url.path == "/api/state":
            return self._json(APP.state())
        if url.path == "/api/data":
            return self._json(data_bundle())
        if url.path == "/api/stream":
            return self._stream(int(parse_qs(url.query).get("since", ["0"])[0]))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/connect":
                return self._json(APP.connect(body.get("api_key", ""), body.get("base_url", ""), body.get("workspace", "")))
            if url.path == "/api/run":
                APP.run(bool(body.get("reset")))
                return self._json(APP.state())
            if url.path == "/api/cleanup":
                APP.cleanup()
                return self._json(APP.state())
            if url.path == "/api/search":
                return self._json(APP.ask(body.get("query", "").strip(), body.get("hire", ""),
                                          (body.get("as_of") or "").strip()))
            if url.path == "/api/audit":
                return self._json({"tables": APP.audit([d for d in body.get("dates") or [] if d])})
            if url.path == "/api/memory":
                return self._json({"scopes": APP.refresh_memory()})
            return self._json({"error": "not found"}, 404)
        except (demo.DemoError, demo.CLIError, RuntimeError, ValueError) as e:
            return self._json({"error": str(e)}, 400)

    def _stream(self, since: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        after = since
        try:
            while True:
                events = APP.bus.wait(after, 15)
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for ev in events:
                    self.wfile.write(f"id: {ev['id']}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n".encode("utf-8"))
                    after = ev["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    ap = argparse.ArgumentParser(description="Web companion for the onboarding-memory demo")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the page automatically")
    args = ap.parse_args()
    demo.find_binary()  # fail early, with the install hint, if the CLI is missing
    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as e:
            if e.errno not in (48, 98, 10048):  # EADDRINUSE on macOS / Linux / Windows
                raise
            print(f"port {port} is in use (another copy of this server, probably); trying {port + 1}", flush=True)
    if httpd is None:
        print(f"error: no free port between {args.port} and {args.port + 19}; pass --port N", file=sys.stderr)
        sys.exit(1)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"onboarding-memory web demo on {url}  (Ctrl-C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except demo.DemoError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

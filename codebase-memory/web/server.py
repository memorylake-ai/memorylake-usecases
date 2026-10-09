#!/usr/bin/env python3
"""
Companion web app for the codebase-memory demo.

    python3 web/server.py            # opens http://127.0.0.1:8765

It runs the very same pipeline as demo.py (it imports it) and streams every
event — each `memorylake` command, each imported file, each extracted fact,
the pre-commit checks and the day-one answers — to the browser over Server-Sent Events.  Standard library only.

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
        ".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


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
        self.projects: dict[str, dict] = {}   # repo -> {id, name}
        self.facts: dict | None = None
        self.brief: dict | None = None
        demo.EMIT = self.sink

    # ---- the sink demo.py talks to
    def sink(self, kind: str, text: str, data: dict) -> None:
        if kind == "project":
            self.projects[data["repo"]] = {"id": data["id"], "name": data["name"]}
        elif kind == "facts":
            self.facts = data["repos"]
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
            "projects": self.projects, "has_facts": bool(self.facts), "has_brief": bool(self.brief),
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
        for repo in demo.REPOS:
            proj = demo.find_project(self.cli, repo)
            if proj:
                self.projects[repo] = {"id": proj["id"], "name": proj["name"]}
                self.bus.push("project", "", {"repo": repo, "id": proj["id"], "name": proj["name"], "existing": True})
        self.bus.push("connected", "", self.state())
        return self.state()

    def run(self, reset: bool) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli
        if reset:
            self.facts = self.brief = None
        self.start("run", lambda: demo.run_pipeline(cli, reset=reset))

    def cleanup(self) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli

        def body():
            self.bus.push("step", "Cleanup — remove everything the demo created", {"index": 0, "total": 0, "title": "Cleanup"})
            demo.cleanup(cli)
            self.projects = {}
            self.facts = self.brief = None

        self.start("cleanup", body)

    def _projects(self) -> dict[str, str]:
        if not self.cli:
            raise RuntimeError("connect first")
        if len(self.projects) < len(demo.REPOS):
            ids = demo.find_projects(self.cli)  # raises with a hint when the demo has not run yet
            self.projects = {r: {"id": i, "name": demo.REPOS[r]["name"]} for r, i in ids.items()}
        return {r: p["id"] for r, p in self.projects.items()}

    def search(self, query: str, repos: list[str], top_k: int) -> dict:
        """Ask the memory of the chosen repos; every hit is labelled with the repo it came from."""
        projects = {r: i for r, i in self._projects().items() if not repos or r in repos}
        if not projects or not query:
            raise ValueError("pick at least one repo and type a question")
        return demo.ask(self.cli, projects, query, query, top_k, demo.owner_maps(self.cli, projects))

    def refresh_facts(self) -> dict:
        demo.show_facts(self.cli, self._projects())
        return self.facts or {}


APP = App()


def data_bundle() -> dict:
    return {
        "people": {k: dict(key=k, **v) for k, v in demo.PEOPLE.items()},
        "repos": {r: dict(key=r, **spec) for r, spec in demo.REPOS.items()},
        "library_root": demo.LIBRARY_ROOT,
        "files": {r: [dict(path=p.relative_to(demo.REPOS_DIR / r).as_posix(), size=p.stat().st_size,
                           text=p.read_text(encoding="utf-8") if p.suffix == ".md" else None)
                      for p in demo.repo_files(r)] for r in demo.REPOS},
        "threads": demo.load_threads(),
        "proposals": [dict(change=p["change"], query=p["query"]) for p in demo.PROPOSALS],
        "questions": [dict(heading=h, query=q, types=t) for h, q, t in demo.QUESTIONS],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "codebase-memory/1.0"

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
        if url.path.startswith("/repos/"):  # the docs as committed, to open next to the tree
            target = (demo.REPOS_DIR / url.path[len("/repos/"):]).resolve()
            if demo.REPOS_DIR.resolve() not in target.parents:
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
                return self._json(APP.search(body.get("query", "").strip(), list(body.get("repos") or []),
                                             int(body.get("top_k") or 5)))
            if url.path == "/api/facts":
                return self._json({"repos": APP.refresh_facts()})
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
    ap = argparse.ArgumentParser(description="Web companion for the codebase-memory demo")
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
    print(f"codebase-memory web demo on {url}  (Ctrl-C to stop)", flush=True)
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

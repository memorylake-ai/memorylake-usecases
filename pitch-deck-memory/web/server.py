#!/usr/bin/env python3
"""
Companion web app for the pitch-deck-memory demo.

    python3 web/server.py            # opens http://127.0.0.1:8765

Runs the same pipeline as demo.py (it imports it) and streams every event to
the browser over Server-Sent Events.  Standard library only; binds to
localhost; the API key you paste stays in this process and in the demo's
isolated CLI profile (./.memorylake-demo/).
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
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


class Bus:
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
        self.status = "idle"
        self.error: str | None = None
        self.ids: dict | None = None      # pitch library project id once set up
        demo.EMIT = self.sink

    def sink(self, kind: str, text: str, data: dict) -> None:
        if kind == "library":
            self.ids = {"project": data["project"]["id"]}
        elif kind == "cmd":
            print("$ " + text, flush=True)
        self.bus.push(kind, text, data)

    def state(self) -> dict:
        cli = self.cli
        return {
            "connected": cli is not None,
            "team": getattr(cli, "team", None) and {"name": cli.team.get("name"), "role": cli.team.get("caller_role")},
            "workspace": cli and {"id": cli.workspace, "name": getattr(cli, "workspace_name", "") or cli.workspace},
            "status": self.status, "job": self.job, "error": self.error, "ids": self.ids,
            "last_event_id": len(self.bus.events),
        }

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
            except Exception as e:  # noqa: BLE001
                self.status, self.error = "error", str(e)
                self.bus.push("error", str(e), {"job": name})

        threading.Thread(target=body, name=name, daemon=True).start()

    def connect(self, api_key: str, base_url: str, workspace: str) -> dict:
        with self.lock:
            if self.status == "running":
                raise RuntimeError("wait for the running job to finish")
        self.bus.push("step", f"Step 1/{demo.TOTAL}  Connect — API key, team, workspace", {"index": 1, "total": demo.TOTAL, "title": "Connect"})
        self.cli = demo.connect(api_key=api_key or None, base_url=base_url or None, workspace=workspace or None)
        proj = demo.find_project(self.cli)
        if proj:
            self.ids = {"project": proj["id"]}
            self.bus.push("existing", "", {"project": proj["id"]})
        self.bus.push("connected", "", self.state())
        return self.state()

    def run(self, reset: bool) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli
        self.start("run", lambda: demo.run_pipeline(cli, reset=reset))

    def cleanup(self) -> None:
        if not self.cli:
            raise RuntimeError("connect first")
        cli = self.cli

        def body():
            self.bus.push("step", "Cleanup — remove everything the demo created", {"index": 0, "total": 0, "title": "Cleanup"})
            demo.cleanup(cli)
            self.ids = None

        self.start("cleanup", body)

    def ask(self, question: str) -> dict:
        if not self.cli:
            raise RuntimeError("connect first")
        if not self.ids:
            raise RuntimeError("the pitch library is not imported yet — run the demo first")
        if not question:
            raise ValueError("type a question")
        files = demo.load_files()
        hits = demo.passages(self.cli, self.ids["project"], question)
        for h in hits:
            meta = files.get(demo.file_of(h["document"] or "")) or {}
            h.update(file=demo.file_of(h["document"] or ""), client=meta.get("client"), outcome=meta.get("outcome"),
                     vertical=meta.get("vertical"), pitched=meta.get("pitched"), label=demo.short(h["document"] or ""))
        return {"question": question, "passages": hits}


APP = App()


def data_bundle() -> dict:
    files = demo.load_files()
    return {"story": demo.load_story(), "files": files, "prefix": demo.PREFIX,
            "names": {demo.lib_name(f): f for f in files}, "labels": {demo.lib_name(f): demo.short(demo.lib_name(f)) for f in files}}


def slide_text(file: str, page: int) -> dict:
    """One slide (or a debrief's paragraphs), read from the committed file, whose sha256 the demo pins."""
    if file not in demo.load_files():
        raise ValueError("unknown file")
    parts = demo.file_text(file, (demo.SOURCES / file).read_bytes())
    if not 1 <= page <= len(parts):
        raise ValueError("no such slide")
    return {"file": file, "page": page, "count": len(parts), "paragraphs": parts[page - 1], "deck": file.endswith(".pptx")}


class Handler(BaseHTTPRequestHandler):
    server_version = "pitch-deck-memory/1.0"

    def log_message(self, fmt, *args):
        if "/api/stream" in (args[0] if args else "") or self.path.startswith("/static"):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

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
        return json.loads(self.rfile.read(n) if n else b"{}")

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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if url.path.startswith("/static/"):
            return self._file(STATIC / url.path[len("/static/"):])
        if url.path == "/api/state":
            return self._json(APP.state())
        if url.path == "/api/data":
            return self._json(data_bundle())
        if url.path == "/api/slide":
            q = parse_qs(url.query)
            try:
                return self._json(slide_text(q.get("file", [""])[0], int(q.get("page", ["1"])[0])))
            except (ValueError, IndexError) as e:
                return self._json({"error": str(e) or "bad page"}, 400)
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
            if url.path == "/api/ask":
                return self._json(APP.ask(body.get("question", "").strip()))
            return self._json({"error": "not found"}, 404)
        except (demo.DemoError, demo.CLIError, RuntimeError, ValueError) as e:
            return self._json({"error": str(e)}, 400)

    def _stream(self, since: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
    ap = argparse.ArgumentParser(description="Web companion for the pitch-deck-memory demo")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    demo.find_binary()
    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as e:
            if e.errno not in (48, 98, 10048):
                raise
            print(f"port {port} is in use (another copy of this server, probably); trying {port + 1}", flush=True)
    if httpd is None:
        print(f"error: no free port between {args.port} and {args.port + 19}; pass --port N", file=sys.stderr)
        sys.exit(1)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"pitch-deck-memory web demo on {url}  (Ctrl-C to stop)", flush=True)
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

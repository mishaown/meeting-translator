"""Local browser application for recording, captions, translation and playback."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import socket
import sys
import threading
import urllib.request
from urllib.parse import unquote, urlparse
import webbrowser

from app_paths import ASSET_ROOT
from engine import ROOT
from gui_runtime import Application
from model_setup import ModelSetup


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR can allow two apps to listen on the same port.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, app=None):
        super().__init__(address, Handler)
        self.app = app or Application()
        self.setup = ModelSetup(self.app)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def json(self, value, status=200):
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def send_bytes(self, data, mime, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def local_request(self, mutation=False):
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise PermissionError("Use the local application URL.")
        origin = self.headers.get("Origin")
        if origin and origin not in (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ):
            raise PermissionError("Requests must come from this local application.")
        if mutation and (
            self.headers.get("X-Local-App") != "1"
            or not self.headers.get("Content-Type", "").startswith("application/json")
        ):
            raise PermissionError("Use the application controls to make changes.")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.local_request()
            path = unquote(urlparse(self.path).path)
            app = self.server.app
            if path == "/api/status":
                return self.json(app.status())
            if path == "/api/setup":
                return self.json(self.server.setup.snapshot())
            if path == "/api/models":
                from model_catalog import catalog

                return self.json(catalog())
            run_match = re.fullmatch(r"/api/sessions/([^/]+)/runs/([^/]+)", path)
            if run_match:
                return self.json(app.store.run_detail(*run_match.groups()))
            if path == "/api/sessions":
                return self.json({"sessions": app.store.list()})
            if path == "/api/health":
                return self.json({"status": "ok", "app": "meeting-desk"})
            match = re.fullmatch(
                r"/api/sessions/([^/]+)(?:/(audio|export)(?:/([^/]+))?)?", path
            )
            if match:
                identity, action, track = match.groups()
                if action == "audio":
                    return self.audio(app.store.audio_path(identity, track))
                if action == "export":
                    profiles = app.store.profiles(identity)
                    lines = [app.store.meta(identity)["title"], ""]
                    for row in app.store.rows(identity):
                        label = profiles.get(row.get("speaker"), {}).get(
                            "name", row.get("speaker", "Speaker")
                        )
                        lines.extend(
                            [
                                f"[{row.get('start', 0):.1f}s] {label}",
                                row.get("chinese", ""),
                                row.get("english", ""),
                                "",
                            ]
                        )
                    return self.send_bytes(
                        "\n".join(lines).encode("utf-8"),
                        "text/plain; charset=utf-8",
                        extra={
                            "Content-Disposition": 'attachment; filename="meeting-transcript.txt"'
                        },
                    )
                return self.json(app.store.detail(identity))
            files = {
                "/": "index.html",
                "/app.js": "app.js",
                "/style.css": "style.css",
                "/favicon.svg": "favicon.svg",
                "/setup.js": "setup.js",
            }
            if path in files:
                file = ASSET_ROOT / "web" / files[path]
                return self.send_bytes(
                    file.read_bytes(),
                    mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            raise FileNotFoundError("Page not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.failure(error)

    def audio(self, path):
        total = path.stat().st_size
        start, end, status = 0, total - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match or not total or not any(match.groups()):
                return self.send_bytes(
                    b"", "audio/wav", 416, {"Content-Range": f"bytes */{total}"}
                )
            low, high = match.groups()
            if low:
                start = int(low)
                end = min(total - 1, int(high)) if high else total - 1
            else:
                start = max(0, total - int(high))
            if start > end or start >= total:
                return self.send_bytes(
                    b"", "audio/wav", 416, {"Content-Range": f"bytes */{total}"}
                )
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = source.read(min(65536, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16384:
                raise ValueError("Request is too large")
            raw = self.rfile.read(length)
            self.local_request(mutation=True)
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a settings object")
            path = unquote(urlparse(self.path).path)
            app = self.server.app
            if path == "/api/setup":
                return self.json(self.server.setup.start(body))
            if path == "/api/start":
                with app.lock:
                    if self.server.setup.snapshot()["state"] == "running":
                        raise ValueError(
                            "Wait for model setup to finish before recording."
                        )
                    return self.json(app.start(body))
            if path in ("/api/pause", "/api/resume", "/api/stop", "/api/options"):
                return self.json(app.control(path.rsplit("/", 1)[1], body))
            match = re.fullmatch(
                r"/api/sessions/([^/]+)/(rename|speakers|speaker|process|select-run)",
                path,
            )
            if match:
                identity, action = match.groups()
                app.store.path(identity)
                if action == "process":
                    with app.lock:
                        if self.server.setup.snapshot()["state"] == "running":
                            raise ValueError(
                                "Wait for model setup to finish before processing."
                            )
                        return self.json(app.process(identity, body))
                if action == "select-run":
                    return self.json(
                        app.select_run(identity, str(body.get("run_id", "")))
                    )
                if action == "rename":
                    title = str(body.get("title", "")).strip()
                    if not title or len(title) > 120:
                        raise ValueError("Enter a name between 1 and 120 characters")
                    app.store.meta(identity, {"title": title})
                elif action == "speakers":
                    profiles = body.get("speakers", {})
                    if not isinstance(profiles, dict) or len(profiles) > 100:
                        raise ValueError("Invalid speaker list")
                    cleaned = {}
                    for key, value in profiles.items():
                        if not re.fullmatch(
                            r"[A-Za-z0-9_]{1,40}", key
                        ) or not isinstance(value, dict):
                            raise ValueError("Invalid speaker label")
                        cleaned[key] = {
                            "name": str(value.get("name", key))[:80],
                            "label": str(value.get("label", ""))[:40],
                        }
                    app.store.profiles(identity, cleaned)
                else:
                    speaker = str(body.get("speaker", "unknown"))
                    if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", speaker):
                        raise ValueError("Invalid speaker")
                    app.store.patch_row(
                        identity,
                        str(body.get("row_id", "")),
                        {"speaker": speaker, "uncertain": False},
                    )
                return self.json(app.store.detail(identity))
            raise FileNotFoundError("Control not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.failure(error)

    def failure(self, error):
        status = (
            403
            if isinstance(error, PermissionError)
            else (
                404
                if isinstance(error, FileNotFoundError)
                else 400 if isinstance(error, (ValueError, KeyError)) else 500
            )
        )
        try:
            self.json({"error": str(error)}, status)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="Open the browser automatically"
    )
    args = parser.parse_args()
    try:
        server = LocalServer(("127.0.0.1", args.port))
    except OSError:
        url = f"http://127.0.0.1:{args.port}"
        try:
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with http.open(url + "/api/health", timeout=2) as response:
                existing = json.load(response)
            if existing.get("app") == "meeting-desk":
                print(f"Meeting desk is already running at {url}")
                if args.open:
                    webbrowser.open(url)
                return
        except (OSError, ValueError):
            pass
        raise SystemExit(
            f"Port {args.port} is busy. Use --port with another port number."
        )
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Meeting desk is running at {url}", flush=True)
    print(
        "Press Ctrl+C to stop the local app and save an active recording.", flush=True
    )
    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Saving your session…", flush=True)
    finally:
        server.app.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

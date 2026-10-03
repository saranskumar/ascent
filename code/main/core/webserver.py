"""The only HTTP endpoint left: POST /webhook on 127.0.0.1 for Meetily's events.

No UI is served. The handler verifies and records the event, then acks within Meetily's
5 s budget; the work happens on the automation's worker thread.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1_000_000


def make_server(port: int, handle) -> ThreadingHTTPServer:
    """handle(body: bytes, headers) -> (status, text)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "vcs-main"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _reply(self, status: int, text: str):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if host not in ("127.0.0.1", "localhost", "[::1]"):
                return self._reply(403, "bad host")
            if self.path.split("?")[0] != "/webhook":
                return self._reply(404, "not found")
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._reply(413, "too large")
            status, text = handle(self.rfile.read(n), self.headers)
            return self._reply(status, text)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, "ok")
            return self._reply(404, "not found")

    return ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)


def serve_in_thread(port: int, handle) -> ThreadingHTTPServer:
    httpd = make_server(port, handle)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="webhook-http").start()
    return httpd

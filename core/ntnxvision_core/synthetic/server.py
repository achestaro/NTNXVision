"""HTTPS front for `MockApi` — a local mock of Prism Central, Prism Element and NDB.

Binds 127.0.0.1 only (never a routable interface) on an ephemeral port. Two TLS modes,
both using certificates minted on this machine by `certgen` (never shipped or committed):

* ``ca`` — a leaf for 127.0.0.1/localhost issued by the local *NTNXVision Demo CA*. Demo
  mode trusts that CA for the demo hostnames only, so "verify TLS" stays on and still
  succeeds.
* ``self`` — a genuinely self-signed certificate: what a lab Prism Central presents out
  of the box, and what the connection test must classify as ``untrusted_cert``.

The addressed system is chosen by the ``X-NTNXVision-Demo-Host`` header (set by the demo
router), falling back to the request's Host header.
"""

from __future__ import annotations

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ntnxvision_core.synthetic import certgen
from ntnxvision_core.synthetic.mockapi import MockApi

HOST_HEADER = "X-NTNXVision-Demo-Host"


def demo_ca() -> Path:
    """The local demo CA certificate (generating the demo TLS material if needed)."""
    return certgen.ensure() / "demo-ca.pem"


def _context(mode: str) -> ssl.SSLContext:
    certs = certgen.ensure()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if mode == "self":
        ctx.load_cert_chain(certs / "selfsigned.pem", certs / "selfsigned.key")
    else:
        ctx.load_cert_chain(certs / "server.pem", certs / "server.key")
    return ctx


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr logging
        return

    def _serve(self, method: str) -> None:
        parts = urlsplit(self.path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        host = self.headers.get(HOST_HEADER) or (self.headers.get("Host") or "").split(":")[0]
        try:
            status, payload, headers = self.server.api.handle(
                host, method, parts.path, query, body, self.headers.get("Authorization"))
        except Exception as exc:  # noqa: BLE001 — a mock bug must surface as a 500, not a hang
            status, payload, headers = 500, {"message": f"mock error: {exc}"}, {}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — http.server naming
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    api: MockApi

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Clients that drop the connection (a refused self-signed cert, a cancelled run)
        # are expected here; the default handler would print a traceback to stderr.
        return


class MockNutanixServer:
    """One HTTPS listener on 127.0.0.1. Start, read `.port`, stop."""

    def __init__(self, api: MockApi, tls: str = "ca") -> None:
        self.api = api
        self.tls = tls
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> int:
        httpd = _Server(("127.0.0.1", 0), _Handler)
        httpd.api = self.api
        if self.tls:
            httpd.socket = _context(self.tls).wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, name=f"mock-{self.tls}",
                                        daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> MockNutanixServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

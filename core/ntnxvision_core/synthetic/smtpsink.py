"""A local SMTP sink for tests and demo mode (stdlib only).

Binds 127.0.0.1 only (hard-coded: never a routable interface) on an ephemeral port by
default, accepts every message (unless told to reject a recipient) and keeps it in
memory, optionally writing each one to ``<out_dir>/message-<n>.eml``. It never relays
anything anywhere.

Supports EHLO/HELO, STARTTLS (``tls="starttls"``) and implicit TLS (``tls="ssl"``) with the
locally minted demo certificates (`certgen`: ``cert="ca"`` is issued by the demo CA,
``cert="self"`` is self-signed), AUTH PLAIN / LOGIN when credentials are configured,
recipient rejection, a SIZE limit, DATA with dot-unstuffing, RSET, NOOP and QUIT.

Run one with ``ntnxvision smtp-sink --port 0 --out DIR``.
"""

from __future__ import annotations

import base64
import binascii
import email.policy
import json
import os
import socket
import socketserver
import ssl
import threading
import time
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from ntnxvision_core.synthetic import certgen

BIND_HOST = "127.0.0.1"


def _tls_context(cert: str) -> ssl.SSLContext:
    certs = certgen.ensure()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert == "self":
        ctx.load_cert_chain(certs / "selfsigned.pem", certs / "selfsigned.key")
    else:
        ctx.load_cert_chain(certs / "server.pem", certs / "server.key")
    return ctx


def demo_ca_file() -> Path:
    """The CA that issued the sink's ``cert="ca"`` certificate."""
    return certgen.ensure() / "demo-ca.pem"


class _Session(socketserver.StreamRequestHandler):
    server: _Server
    timeout = 30

    def setup(self) -> None:
        self.request.settimeout(self.timeout)
        if self.server.sink.tls == "ssl":
            try:
                self.request = self.server.sink.context().wrap_socket(self.request,
                                                                       server_side=True)
            except (ssl.SSLError, OSError):
                self.request.close()
                self.broken = True
                return
        self.broken = False
        super().setup()

    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def _readline(self) -> bytes | None:
        line = self.rfile.readline(65536)
        return line if line else None

    def handle(self) -> None:  # noqa: C901 - one SMTP state machine
        if getattr(self, "broken", False):
            return
        sink = self.server.sink
        tls_active = sink.tls == "ssl"
        authed = False
        mail_from: str | None = None
        rcpts: list[str] = []
        self._send("220 sink.example.lab NTNXVision test SMTP sink")
        while True:
            try:
                raw = self._readline()
            except (OSError, ssl.SSLError):
                return
            if raw is None:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            verb, _, arg = line.partition(" ")
            verb = verb.upper()
            if verb in ("EHLO", "HELO"):
                if verb == "HELO":
                    self._send("250 sink.example.lab")
                    continue
                caps = ["sink.example.lab", f"SIZE {sink.max_size}", "8BITMIME"]
                if sink.tls == "starttls" and not tls_active:
                    caps.append("STARTTLS")
                if sink.username:
                    caps.append("AUTH PLAIN LOGIN")
                for i, cap in enumerate(caps):
                    self._send(f"250{'-' if i < len(caps) - 1 else ' '}{cap}")
            elif verb == "STARTTLS":
                if sink.tls != "starttls" or tls_active:
                    self._send("502 STARTTLS not available")
                    continue
                self._send("220 Ready to start TLS")
                try:
                    self.request = sink.context().wrap_socket(self.request, server_side=True)
                except (ssl.SSLError, OSError):
                    return
                self.rfile = self.request.makefile("rb")
                self.wfile = self.request.makefile("wb")
                tls_active = True
                authed, mail_from, rcpts = False, None, []
            elif verb == "AUTH":
                if not sink.username:
                    self._send("503 AUTH not available")
                    continue
                authed = self._auth(arg)
                if not authed and sink.auth_fail_closes:
                    # Gmail answers a bad sign-in with 535 and hangs up.
                    return
            elif verb == "MAIL":
                if sink.require_tls and not tls_active:
                    self._send("530 5.7.0 Must issue a STARTTLS command first.")
                    continue
                if sink.username and not authed:
                    self._send("530 5.7.0 Authentication required")
                    continue
                mail_from = arg.partition(":")[2].split(" ")[0].strip("<>")
                rcpts = []
                self._send("250 OK")
            elif verb == "RCPT":
                if mail_from is None:
                    self._send("503 MAIL first")
                    continue
                addr = arg.partition(":")[2].strip().strip("<>")
                if addr.casefold() in sink.reject:
                    self._send(f"550 5.1.1 <{addr}>: Recipient address rejected")
                    continue
                rcpts.append(addr)
                self._send("250 OK")
            elif verb == "DATA":
                if not rcpts:
                    self._send("503 RCPT first")
                    continue
                self._send("354 End data with <CR><LF>.<CR><LF>")
                data = self._data()
                if data is None:
                    return
                if len(data) > sink.max_size:
                    self._send("552 5.3.4 Message size exceeds fixed limit")
                else:
                    sink.store(mail_from or "", rcpts, data)
                    self._send("250 OK queued")
                mail_from, rcpts = None, []
            elif verb == "RSET":
                mail_from, rcpts = None, []
                self._send("250 OK")
            elif verb == "NOOP":
                self._send("250 OK")
            elif verb == "QUIT":
                self._send("221 Bye")
                return
            else:
                self._send("500 Command not recognized")

    def _data(self) -> bytes | None:
        lines: list[bytes] = []
        while True:
            raw = self._readline()
            if raw is None:
                return None
            if raw in (b".\r\n", b".\n"):
                return b"".join(lines)
            if raw.startswith(b".."):
                raw = raw[1:]
            lines.append(raw)

    def _auth(self, arg: str) -> bool:
        sink = self.server.sink
        mech, _, initial = arg.partition(" ")
        mech = mech.upper()
        try:
            if mech == "PLAIN":
                if not initial:
                    self._send("334 ")
                    raw = self._readline() or b""
                    initial = raw.decode().strip()
                parts = base64.b64decode(initial).split(b"\0")
                user, pw = parts[-2].decode(), parts[-1].decode()
            elif mech == "LOGIN":
                if initial:
                    user = base64.b64decode(initial).decode()
                else:
                    self._send("334 VXNlcm5hbWU6")
                    user = base64.b64decode((self._readline() or b"").strip()).decode()
                self._send("334 UGFzc3dvcmQ6")
                pw = base64.b64decode((self._readline() or b"").strip()).decode()
            else:
                self._send("504 Unrecognized authentication type")
                return False
        except (binascii.Error, IndexError, UnicodeDecodeError):
            self._send("501 Malformed AUTH")
            return False
        if user == sink.username and pw == sink.password:
            self._send("235 2.7.0 Authentication successful")
            return True
        self._send("535 5.7.8 Authentication credentials invalid")
        return False


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    sink: SmtpSink

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that drops mid-session (a failed TLS test) is not worth a traceback."""
        return


class SmtpSink:
    """A throwaway SMTP server on 127.0.0.1 that keeps what it receives."""

    def __init__(self, *, port: int = 0, tls: str = "none", cert: str = "ca",
                 username: str = "", password: str = "", reject: list[str] | None = None,
                 max_size: int = 50 * 1024 * 1024, out_dir: str | Path | None = None,
                 auth_fail_closes: bool = False, require_tls: bool = False) -> None:
        if tls not in ("none", "starttls", "ssl"):
            raise ValueError(f"tls must be none, starttls or ssl, not {tls!r}")
        self.port = port
        self.tls = tls
        self.cert = cert
        self.username = username
        self.password = password
        self.reject = {r.casefold() for r in reject or []}
        self.max_size = max_size
        #: Close the connection after a rejected sign-in (as Gmail does).
        self.auth_fail_closes = auth_fail_closes
        #: Refuse MAIL FROM before STARTTLS with Gmail's 530 reply.
        self.require_tls = require_tls
        self.out_dir = Path(out_dir) if out_dir else None
        self.messages: list[dict[str, Any]] = []
        self._lock = threading.Condition()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._ctx: ssl.SSLContext | None = None

    def context(self) -> ssl.SSLContext:
        if self._ctx is None:
            self._ctx = _tls_context(self.cert)
        return self._ctx

    def start(self) -> int:
        if self.tls != "none":
            self.context()
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        server = _Server((BIND_HOST, self.port), _Session)
        server.sink = self
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="smtp-sink")
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def store(self, mail_from: str, rcpts: list[str], data: bytes) -> None:
        with self._lock:
            self.messages.append({"mail_from": mail_from, "rcpt_to": list(rcpts), "data": data})
            n = len(self.messages)
            if self.out_dir:
                path = self.out_dir / f"message-{n}.eml"
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
                # The summary is written after the .eml: a reader polling for *.json
                # always finds the full message next to it.
                summary = path.with_suffix(".json")
                tmp = summary.with_name(summary.name + ".tmp")
                tmp.write_text(json.dumps(describe(mail_from, rcpts, data), indent=2),
                               encoding="utf-8")
                os.replace(tmp, summary)
            self._lock.notify_all()

    def wait_for(self, n: int, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._lock:
            while len(self.messages) < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._lock.wait(left)
            return True

    def __enter__(self) -> SmtpSink:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def describe(mail_from: str, rcpts: list[str], data: bytes) -> dict[str, Any]:
    """A received message as JSON-friendly fields (decoded subject and file names)."""
    msg = BytesParser(policy=email.policy.default).parsebytes(data)
    attachments = []
    body_text = ""
    if isinstance(msg, EmailMessage):
        for part in msg.iter_attachments():
            payload = part.get_payload(decode=True)
            attachments.append({"filename": part.get_filename(),
                                "content_type": part.get_content_type(),
                                "size": len(payload) if isinstance(payload, bytes) else 0})
        body = msg.get_body(("plain",))
        if body is not None:
            body_text = str(body.get_content())
    return {"mail_from": mail_from, "rcpt_to": list(rcpts), "subject": str(msg["Subject"] or ""),
            "from": str(msg["From"] or ""), "to": str(msg["To"] or ""),
            "cc": str(msg["Cc"] or ""), "attachments": attachments, "body_text": body_text}


def closed_port() -> int:
    """A 127.0.0.1 port with nothing listening (for connection-refused tests)."""
    with socket.socket() as s:
        s.bind((BIND_HOST, 0))
        return int(s.getsockname()[1])

"""Verified TLS trust store (tlsctx.py): OS store first, certifi fallback, and the frozen
sidecar case where OpenSSL's default CA paths do not exist."""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from ntnxvision_core import mailer, tlsctx
from ntnxvision_core.api import transport
from ntnxvision_core.synthetic import certgen
from ntnxvision_core.synthetic.smtpsink import SmtpSink, demo_ca_file

OS_PLATFORM = sys.platform in ("darwin", "win32")

# Runs in a child interpreter whose OpenSSL default CA paths point at nothing: the state
# of the frozen sidecar on a user's machine (OPENSSLDIR names the build machine's Python).
_FROZEN_PROBE = r"""
import json, ssl
from ntnxvision_core import tlsctx
stdlib = ssl.create_default_context()
ctx = tlsctx.verified_context()
try:
    helper_roots = ctx.cert_store_stats()["x509_ca"]
except NotImplementedError:  # truststore: the OS store is not an OpenSSL store
    helper_roots = None
print(json.dumps({
    "stdlib_roots": stdlib.cert_store_stats()["x509_ca"],
    "helper_roots": helper_roots,
    "helper_verifies": ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname,
    "summary": tlsctx.trust_summary(),
}))
"""


def _frozen_env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "no-certs"
    empty.mkdir()
    env = dict(os.environ)
    env["SSL_CERT_FILE"] = str(empty / "missing.pem")
    env["SSL_CERT_DIR"] = str(empty)
    env.pop("REQUESTS_CA_BUNDLE", None)
    env.pop("CURL_CA_BUNDLE", None)
    return env


def test_frozen_simulation_still_has_a_trust_store(tmp_path: Path) -> None:
    out = subprocess.run([sys.executable, "-c", _FROZEN_PROBE], env=_frozen_env(tmp_path),
                         capture_output=True, text=True, timeout=60, check=True)
    probe = json.loads(out.stdout)
    if sys.platform != "win32":
        # The bug: with OpenSSL's paths empty the stdlib default trusts nothing.
        # (Windows stdlib reads the Windows store, so it is not affected.)
        assert probe["stdlib_roots"] == 0, probe
    assert probe["helper_verifies"]
    summary = probe["summary"]
    assert summary["ok"], summary
    assert summary["certifi_roots"] > 100
    if OS_PLATFORM:
        assert summary["backend"] == "os" and summary["os_roots"] > 0
    else:
        assert probe["helper_roots"] > 100


def test_fallback_without_truststore_loads_certifi(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    monkeypatch.setattr(tlsctx, "truststore", None)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    assert not tlsctx.uses_os_store()
    ctx = tlsctx.verified_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 100
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.skipif(not OS_PLATFORM, reason="OS trust store only on macOS / Windows")
def test_os_store_backend_on_desktop_platforms() -> None:
    assert tlsctx.uses_os_store()
    ctx = tlsctx.verified_context()
    assert type(ctx).__module__.startswith("truststore")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


# ─── a local HTTPS server signed by the demo CA (not trusted by the OS) ─


@pytest.fixture
def https_server() -> Iterator[int]:
    certs = certgen.ensure()
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(certs / "server.pem", certs / "server.key")
    lsock = socket.create_server(("127.0.0.1", 0))
    port = lsock.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        lsock.settimeout(0.2)
        while not stop.is_set():
            try:
                raw, _ = lsock.accept()
            except OSError:
                continue
            try:
                with sctx.wrap_socket(raw, server_side=True) as conn:
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                                 b"Connection: close\r\n\r\nok")
            except (OSError, ssl.SSLError):
                pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield port
    finally:
        stop.set()
        t.join(2)
        lsock.close()


def _verified_server_name() -> str:
    # The demo server certificate names localhost / 127.0.0.1 among its SANs.
    return "localhost"


def test_session_verify_true_uses_the_helper(https_server: int,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[ssl.SSLContext] = []
    real = tlsctx.verified_context

    def spy(cafile: str | None = None) -> ssl.SSLContext:
        ctx = real(cafile)
        built.append(ctx)
        return ctx

    monkeypatch.setattr(tlsctx, "verified_context", spy)
    session = transport.new_session()
    assert isinstance(session.get_adapter("https://x"), tlsctx.TrustStoreAdapter)
    with pytest.raises(requests.exceptions.SSLError):
        session.get(f"https://{_verified_server_name()}:{https_server}/", timeout=5)
    assert built, "verify=True did not go through verified_context()"


def test_session_trusts_an_extra_anchor_through_the_helper(
        https_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stands in for an enterprise CA: once the helper trusts it, verified requests pass.
    real = tlsctx.verified_context
    monkeypatch.setattr(tlsctx, "verified_context",
                        lambda cafile=None: real(str(demo_ca_file())))
    session = transport.new_session()
    resp = session.get(f"https://{_verified_server_name()}:{https_server}/", timeout=5)
    assert resp.status_code == 200 and resp.text == "ok"


def test_pinned_session_does_not_use_the_helper(https_server: int,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cafile: str | None = None) -> Any:
        raise AssertionError("verification off must not build a verified context")

    monkeypatch.setattr(tlsctx, "verified_context", boom)
    der = ssl.PEM_cert_to_DER_cert((certgen.ensure() / "server.pem").read_text())
    import hashlib

    session = transport.new_session()
    transport.configure_tls(session, False, hashlib.sha256(der).hexdigest())
    resp = session.get(f"https://127.0.0.1:{https_server}/", timeout=5)
    assert resp.status_code == 200


# ─── mailer ────────────────────────────────────────────────────────────


def _smtp(port: int, **kw: Any) -> mailer.SmtpConfig:
    return mailer.SmtpConfig(host="127.0.0.1", port=port, from_address="n@example.com",
                             timeout=5.0, **kw)


def test_mailer_verified_context_comes_from_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    real = tlsctx.verified_context

    def spy(cafile: str | None = None) -> ssl.SSLContext:
        seen.append(cafile)
        return real(cafile)

    monkeypatch.setattr(mailer, "verified_context", spy)
    with SmtpSink(tls="starttls") as s:
        r = mailer.send_test(_smtp(s.port, security="starttls", cafile=str(demo_ca_file())),
                             "user@example.com")
        assert r["ok"], r
    assert seen == [str(demo_ca_file())]


def test_mailer_allow_untrusted_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mailer, "verified_context",
                        lambda cafile=None: pytest.fail("allow_untrusted must not verify"))
    ctx = mailer._context(_smtp(25, security="starttls", allow_untrusted=True))
    assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname
    with SmtpSink(tls="starttls", cert="self") as s:
        r = mailer.send_test(_smtp(s.port, security="starttls", allow_untrusted=True),
                             "user@example.com")
        # Unpinned: the certificate is shown for trust, nothing sent.
        assert r["status"] == "untrusted_cert" and not s.messages
        pin = r["certificate"]["fingerprint_sha256"].replace(":", "").lower()
        r = mailer.send_test(_smtp(s.port, security="starttls", allow_untrusted=True,
                                   pinned_sha256=pin), "user@example.com")
        assert r["ok"], r


def test_mailer_untrusted_server_is_still_refused() -> None:
    with SmtpSink(tls="ssl", cert="self") as s:
        r = mailer.send_test(_smtp(s.port, security="ssl"), "user@example.com")
        assert r["status"] == "untrusted_cert", r

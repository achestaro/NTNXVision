"""SMTP trust on first use (R2-03): an untrusted certificate is accepted only when it matches
the fingerprint the user pinned, checked after the handshake and before any sign-in."""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core import mailer
from ntnxvision_core.synthetic import certgen
from ntnxvision_core.synthetic.smtpsink import SmtpSink


def cfg(port: int, **kw: Any) -> mailer.SmtpConfig:
    base: dict[str, Any] = {"host": "127.0.0.1", "port": port, "security": "starttls",
                            "from_address": "ntnxvision@example.com", "timeout": 5.0,
                            "allow_untrusted": True}
    base.update(kw)
    return mailer.SmtpConfig(**base)


def leaf_sha256(name: str = "selfsigned.pem") -> str:
    pem = (certgen.ensure() / name).read_text()
    first = pem.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----"
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(first.strip())).hexdigest()


def sink(**kw: Any) -> SmtpSink:
    return SmtpSink(cert="self", username="u", password="p", **kw)


@pytest.mark.parametrize("security", ["starttls", "ssl"])
def test_probe_returns_the_certificate_and_sends_nothing(security: str) -> None:
    with sink(tls=security) as s:
        r = mailer.send_test(cfg(s.port, security=security, username="u", password="p"),
                             "user@example.com")
        assert not r["ok"] and r["status"] == "untrusted_cert"
        cert = r["certificate"]
        assert cert["fingerprint_sha256"].replace(":", "").lower() == leaf_sha256()
        assert cert["subject"] and cert["not_after"]
        assert s.messages == []


def test_matching_pin_sends() -> None:
    with sink(tls="starttls") as s:
        r = mailer.send_test(cfg(s.port, username="u", password="p",
                                 pinned_sha256=leaf_sha256()), "user@example.com")
        assert r["ok"], r
        assert len(s.messages) == 1


def test_matching_pin_implicit_ssl() -> None:
    with sink(tls="ssl") as s:
        r = mailer.send_test(cfg(s.port, security="ssl", username="u", password="p",
                                 pinned_sha256=leaf_sha256()), "user@example.com")
        assert r["ok"], r


def test_mismatch_refuses_before_login_and_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    logins: list[str] = []
    real = mailer.smtplib.SMTP.login

    def spy(self: Any, *a: Any, **k: Any) -> Any:
        logins.append("login")
        return real(self, *a, **k)

    monkeypatch.setattr(mailer.smtplib.SMTP, "login", spy)
    with sink(tls="starttls") as s:
        r = mailer.send_test(
            cfg(s.port, username="u", password="p", pinned_sha256=leaf_sha256("server.pem")),
            "user@example.com")
        assert not r["ok"] and r["status"] == "untrusted_cert"
        assert "changed" in r["message"]
        assert r["certificate"]["fingerprint_sha256"].replace(":", "").lower() == leaf_sha256()
        assert s.messages == [] and logins == []


def test_send_without_a_pin_is_refused_with_guidance(tmp_path: Path) -> None:
    f = tmp_path / "r.zip"
    f.write_bytes(b"x")
    with sink(tls="starttls") as s:
        r = mailer.send_files(cfg(s.port, username="u", password="p"), ["a@example.com"], [],
                              "Subject", "Body", [f])
        assert not r["ok"] and r["status"] == "untrusted_cert"
        assert "Settings > Email" in r["message"] and "Trust this certificate" in r["message"]
        assert s.messages == []


def test_send_files_with_pin_and_with_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "r.zip"
    f.write_bytes(b"x")
    with sink(tls="starttls") as s:
        ok = mailer.send_files(cfg(s.port, username="u", password="p",
                                   pinned_sha256=leaf_sha256()),
                               ["a@example.com"], [], "Subject", "Body", [f])
        assert ok["ok"], ok
        bad = mailer.send_files(cfg(s.port, username="u", password="p",
                                    pinned_sha256="ab" * 32),
                                ["a@example.com"], [], "Subject", "Body", [f])
        assert bad["status"] == "untrusted_cert" and "changed" in bad["message"]
        assert len(s.messages) == 1


def test_verified_mode_is_unchanged() -> None:
    with sink(tls="starttls") as s:
        r = mailer.send_test(cfg(s.port, allow_untrusted=False, pinned_sha256=leaf_sha256()),
                             "user@example.com")
        assert r["status"] == "untrusted_cert" and "Allow untrusted certificate" in r["message"]


def test_from_params_normalizes_the_pin() -> None:
    raw = {"host": "mail.example.com", "from_address": "a@example.com", "allow_untrusted": True}
    fp = "AB:" * 31 + "AB"
    assert mailer.SmtpConfig.from_params({**raw, "pinned_sha256": fp}).pinned_sha256 == "ab" * 32
    assert mailer.SmtpConfig.from_params({**raw, "pinned_sha256": "nope"}).pinned_sha256 == ""

"""SMTP: message building, size guard, TLS modes, auth, error classification (mailer.py),
against the local test sink (synthetic/smtpsink.py, 127.0.0.1 only)."""

from __future__ import annotations

import email
import json
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core import mailer
from ntnxvision_core.synthetic.smtpsink import SmtpSink, closed_port, demo_ca_file

PASSWORD = "s3cret-Example"


def cfg(port: int, **kw: Any) -> mailer.SmtpConfig:
    base: dict[str, Any] = {"host": "127.0.0.1", "port": port, "security": "none",
                            "from_address": "ntnxvision@example.com", "from_name": "NTNXVision",
                            "timeout": 5.0}
    base.update(kw)
    return mailer.SmtpConfig(**base)


@pytest.fixture
def sink() -> Iterator[SmtpSink]:
    with SmtpSink() as s:
        yield s


def parse(raw: bytes) -> EmailMessage:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    return msg


# ─── building ──────────────────────────────────────────────────────────


def test_build_message_headers_and_attachments(tmp_path: Path) -> None:
    files = []
    for name in ("r.xlsx", "r.pdf", "r.docx", "r.zip", "r.csv"):
        p = tmp_path / name
        p.write_bytes(b"x" * 10)
        files.append(p)
    c = cfg(25, from_name="Ops Reports")
    msg = mailer.build_message(c, ["user@example.com"], ["cc@example.com"], "Subject",
                               "Body text", files)
    assert msg["From"] == "Ops Reports <ntnxvision@example.com>"
    assert msg["To"] == "user@example.com"
    assert msg["Cc"] == "cc@example.com"
    assert msg["Message-ID"].endswith("@example.com>")
    assert msg["Date"]
    types = {a.get_filename(): a.get_content_type() for a in msg.iter_attachments()}
    assert types == {
        "r.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "r.pdf": "application/pdf",
        "r.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "r.zip": "application/zip",
        "r.csv": "text/csv",
    }
    body = msg.get_body(("plain",))
    assert body is not None and body.get_content().strip() == "Body text"


def test_render_template_keeps_unknown_braces() -> None:
    out = mailer.render_template("{target} on {date}: {snapshot} {unknown} {} {{x}} {0}",
                                 {"target": "pc01", "date": "2026-10-17", "snapshot": "Q4"})
    assert out == "pc01 on 2026-10-17: Q4 {unknown} {} {{x}} {0}"
    assert mailer.render_template("{summary}", {}) == "{summary}"


def test_from_params_validation() -> None:
    with pytest.raises(mailer.MailError) as e:
        mailer.SmtpConfig.from_params({"host": ""})
    assert e.value.status == "not_configured"
    with pytest.raises(mailer.MailError) as e:
        mailer.SmtpConfig.from_params({"host": "smtp.example.lab", "from_address": "nope"})
    assert e.value.status == "invalid"
    with pytest.raises(mailer.MailError) as e:
        mailer.SmtpConfig.from_params({"host": "smtp.example.lab", "security": "tls",
                                       "from_address": "a@example.com"})
    assert e.value.status == "invalid"
    c = mailer.SmtpConfig.from_params({"host": "smtp.example.lab", "security": "ssl",
                                       "from_address": "a@example.com"})
    assert c.port == 465 and c.max_attachment_mb == 20


def test_addresses() -> None:
    assert mailer.is_valid_address("user@example.com")
    assert mailer.is_valid_address("ops@localhost")
    for bad in ("user", "user@", "@example.com", "a b@example.com", "user@example",
                "user@.example.com", "a@b..com"):
        assert not mailer.is_valid_address(bad), bad
    assert mailer.clean_addresses("a@example.com; B@example.com,b@example.com\nc@example.com") == \
        ["a@example.com", "B@example.com", "c@example.com"]


# ─── size guard ────────────────────────────────────────────────────────


def test_size_guard_refuses_before_connecting(tmp_path: Path) -> None:
    big = tmp_path / "big.xlsx"
    big.write_bytes(b"\0" * (2 * 1024 * 1024 + 1))
    # A closed port: had it tried to connect, the status would be unreachable.
    r = mailer.send_files(cfg(closed_port(), max_attachment_mb=2), ["user@example.com"], [],
                          "s", "b", [big])
    assert r["status"] == "too_large" and not r["ok"]
    assert "over the 2 MB limit" in r["message"] and "Zip attachments" in r["message"]


def test_zip_brings_it_under_the_limit(tmp_path: Path, sink: SmtpSink) -> None:
    big = tmp_path / "big.xlsx"
    big.write_bytes(b"\0" * (2 * 1024 * 1024 + 1))
    r = mailer.send_files(cfg(sink.port, max_attachment_mb=2, zip_attachments=True),
                          ["user@example.com"], [], "s", "b", [big], zip_name="pc01-reports")
    assert r["ok"], r
    assert r["zipped"] and r["attachments"][0]["name"] == "pc01-reports.zip"
    msg = parse(sink.messages[0]["data"])
    names = [a.get_filename() for a in msg.iter_attachments()]
    assert names == ["pc01-reports.zip"]


# ─── TLS modes ─────────────────────────────────────────────────────────


def test_plain_send(sink: SmtpSink, tmp_path: Path) -> None:
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    r = mailer.send_files(cfg(sink.port), ["user@example.com"], ["cc@example.com"],
                          "Hello", "Body", [f])
    assert r["ok"] and r["status"] == "ok"
    assert r["recipients"] == ["user@example.com", "cc@example.com"]
    assert r["message"] == "Sent 1 file to 2 recipients."
    assert sink.messages[0]["rcpt_to"] == ["user@example.com", "cc@example.com"]


def test_starttls_with_trusted_ca() -> None:
    with SmtpSink(tls="starttls") as s:
        r = mailer.send_test(cfg(s.port, security="starttls", cafile=str(demo_ca_file())),
                             "user@example.com")
        assert r["ok"], r
        assert len(s.messages) == 1


def test_starttls_self_signed_untrusted_then_allowed() -> None:
    with SmtpSink(tls="starttls", cert="self") as s:
        r = mailer.send_test(cfg(s.port, security="starttls"), "user@example.com")
        assert r["status"] == "untrusted_cert" and "Allow untrusted certificate" in r["message"]
        r = mailer.send_test(cfg(s.port, security="starttls", allow_untrusted=True),
                             "user@example.com")
        assert r["ok"], r


def test_implicit_ssl() -> None:
    with SmtpSink(tls="ssl") as s:
        r = mailer.send_test(cfg(s.port, security="ssl", cafile=str(demo_ca_file())),
                             "user@example.com")
        assert r["ok"], r


def test_ssl_client_against_plain_server(sink: SmtpSink) -> None:
    r = mailer.send_test(cfg(sink.port, security="ssl"), "user@example.com")
    assert r["status"] == "tls_error", r
    assert "Security setting" in r["message"]


def test_starttls_not_offered(sink: SmtpSink) -> None:
    r = mailer.send_test(cfg(sink.port, security="starttls"), "user@example.com")
    assert r["status"] == "tls_error"
    assert "does not offer STARTTLS" in r["message"]


# ─── auth ──────────────────────────────────────────────────────────────


@pytest.fixture
def auth_sink() -> Iterator[SmtpSink]:
    with SmtpSink(username="mailer", password=PASSWORD) as s:
        yield s


def test_auth_ok(auth_sink: SmtpSink) -> None:
    r = mailer.send_test(cfg(auth_sink.port, username="mailer", password=PASSWORD),
                         "user@example.com")
    assert r["ok"], r


def test_auth_failed_never_echoes_password(auth_sink: SmtpSink) -> None:
    r = mailer.send_test(cfg(auth_sink.port, username="mailer", password="wrong-" + PASSWORD),
                         "user@example.com")
    assert r["status"] == "auth_failed"
    assert PASSWORD not in repr(r)


def test_auth_required(auth_sink: SmtpSink) -> None:
    r = mailer.send_test(cfg(auth_sink.port), "user@example.com")
    assert r["status"] == "auth_required", r


# ─── recipients ────────────────────────────────────────────────────────


def test_recipient_rejected_all_and_partial(tmp_path: Path) -> None:
    with SmtpSink(reject=["nobody@example.com"]) as s:
        r = mailer.send_files(cfg(s.port), ["nobody@example.com"], [], "s", "b", [])
        assert r["status"] == "recipient_rejected" and r["rejected"] == ["nobody@example.com"]
        r = mailer.send_files(cfg(s.port), ["user@example.com", "nobody@example.com"], [],
                              "s", "b", [])
        assert r["status"] == "recipient_rejected" and not r["ok"]
        assert r["rejected"] == ["nobody@example.com"]
        assert "1 of 2 recipients" in r["message"]
        assert s.messages[-1]["rcpt_to"] == ["user@example.com"]


def test_invalid_recipient_is_refused_locally() -> None:
    r = mailer.send_files(cfg(closed_port()), ["not-an-address"], [], "s", "b", [])
    assert r["status"] == "invalid"


# ─── network failures ──────────────────────────────────────────────────


def test_connection_refused() -> None:
    r = mailer.send_test(cfg(closed_port()), "user@example.com")
    assert r["status"] == "unreachable" and r["detail"] == "Connection refused"


def test_timeout_when_server_never_greets() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    conns: list[socket.socket] = []
    t = threading.Thread(target=lambda: conns.append(srv.accept()[0]), daemon=True)
    t.start()
    try:
        r = mailer.send_test(cfg(srv.getsockname()[1], timeout=0.5), "user@example.com")
        assert r["status"] == "timeout", r
    finally:
        for c in conns:
            c.close()
        srv.close()


def test_demo_mode_refuses_non_local_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NTNXVISION_DEMO", "1")
    r = mailer.send_test(cfg(25, host="smtp.example.lab"), "user@example.com")
    assert r["status"] == "invalid" and "Demo mode" in r["message"]
    r = mailer.send_test(cfg(25, host="192.0.2.10"), "user@example.com")
    assert r["status"] == "invalid"


def test_demo_mode_allows_the_local_sink(monkeypatch: pytest.MonkeyPatch,
                                         sink: SmtpSink) -> None:
    monkeypatch.setenv("NTNXVISION_DEMO", "1")
    assert mailer.send_test(cfg(sink.port), "user@example.com")["ok"]


# ─── sink ──────────────────────────────────────────────────────────────


def test_sink_writes_eml_files(tmp_path: Path) -> None:
    out = tmp_path / "mail"
    with SmtpSink(out_dir=out) as s:
        assert mailer.send_test(cfg(s.port), "user@example.com")["ok"]
        assert s.wait_for(1, 5)
    files = sorted(p.name for p in out.iterdir())
    assert files == ["message-1.eml", "message-1.json"]
    assert parse((out / "message-1.eml").read_bytes())["Subject"] == "NTNXVision test email"
    info = json.loads((out / "message-1.json").read_text())
    assert info["subject"] == "NTNXVision test email"
    assert info["rcpt_to"] == ["user@example.com"] and info["attachments"] == []
    assert info["mail_from"] == "ntnxvision@example.com" and "Settings > Email" in info["body_text"]


def test_sink_json_lists_attachments(tmp_path: Path) -> None:
    f = tmp_path / "pc01 Q4-2026-10-17.xlsx"
    f.write_bytes(b"x" * 123)
    with SmtpSink(out_dir=tmp_path / "mail") as s:
        r = mailer.send_files(cfg(s.port), ["user@example.com"], ["cc@example.com"],
                              "Résumé {x}", "b", [f])
        assert r["ok"]
    info = json.loads((tmp_path / "mail" / "message-1.json").read_text())
    assert info["subject"] == "Résumé {x}" and info["cc"] == "cc@example.com"
    assert info["attachments"] == [{
        "filename": f.name, "size": 123,
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]


def test_sink_size_limit(tmp_path: Path) -> None:
    f = tmp_path / "r.xlsx"
    f.write_bytes(b"y" * 4096)
    with SmtpSink(max_size=1024) as s:
        r = mailer.send_files(cfg(s.port), ["user@example.com"], [], "s", "b", [f])
        assert r["status"] == "too_large", r


def test_sink_cli_prints_its_port() -> None:
    proc = subprocess.Popen([sys.executable, "-m", "ntnxvision_core", "smtp-sink", "--port", "0"],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line.startswith("SMTP sink listening on 127.0.0.1:")
        port = int(line.rsplit(":", 1)[1])
        assert mailer.send_test(cfg(port), "user@example.com")["ok"]
    finally:
        proc.kill()
        proc.wait(timeout=10)

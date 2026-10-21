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
        # Untrusted is allowed only pinned: the first test shows the certificate (R2-03).
        assert r["status"] == "untrusted_cert" and not s.messages
        pin = r["certificate"]["fingerprint_sha256"]
        r = mailer.send_test(cfg(s.port, security="starttls", allow_untrusted=True,
                                 pinned_sha256=pin.replace(":", "").lower()), "user@example.com")
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
        # Delivered to the others: sent with warnings, not failed.
        assert r["status"] == "sent_with_warnings" and r["ok"], r
        assert r["rejected"] == ["nobody@example.com"]
        assert r["recipients"] == ["user@example.com"]
        assert "to 1 of 2 recipients" in r["message"] and "nobody@example.com" in r["message"]
        assert "550 5.1.1" in (r["detail"] or "")
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


# ─── SEC-10 / SEC-23 ───────────────────────────────────────────────────


def test_no_login_over_cleartext_to_a_remote_server() -> None:
    c = cfg(25, host="192.0.2.25", username="mailer", password=PASSWORD)
    with pytest.raises(mailer.MailError) as ei:
        mailer._connect(c)
    assert ei.value.status == "auth_failed"
    assert "STARTTLS or SSL/TLS" in str(ei.value)
    assert PASSWORD not in str(ei.value)
    assert "\u2014" not in str(ei.value)
    r = mailer.send_test(c, "ops@example.com")
    assert not r["ok"] and r["status"] == "auth_failed"


def test_cleartext_is_fine_without_a_login_or_on_loopback() -> None:
    for host in ("127.0.0.1", "127.9.9.9", "localhost", "::1", "[::1]"):
        assert mailer.is_loopback_host(host)
    assert not mailer.is_loopback_host("192.0.2.25")
    assert not mailer.is_loopback_host("mail.example.lab")
    mailer._guard_cleartext_auth(cfg(25, host="192.0.2.25"))  # no username: allowed
    mailer._guard_cleartext_auth(cfg(25, host="127.0.0.1", username="u", password="p"))


def test_subject_is_one_capped_line() -> None:
    ctx = {"summary": "Summary:\nsnap-a: 3 VMs\r\nsnap-b: 4 VMs", "target": "pc01"}
    s = mailer.render_subject("Report {target}\n{summary}", ctx)
    assert "\n" not in s and "\r" not in s
    assert s == "Report pc01 Summary: snap-a: 3 VMs snap-b: 4 VMs"
    assert len(mailer.render_subject("x" * 500, {})) == mailer.MAX_SUBJECT
    msg = mailer.build_message(cfg(25), ["a@example.com"], [], "a\nBcc: evil@example.com", "b")
    assert "\n" not in str(msg["Subject"])


# ─── QA round: classification ──────────────────────────────────────────


def test_gmail_style_wrong_password_is_a_sign_in_failure() -> None:
    """Gmail offers PLAIN and LOGIN, answers the first with 535 and hangs up: smtplib's
    fallback to LOGIN then sees a closed connection. That is a rejected sign-in, never a
    Security/port mismatch, and the 535 reply is kept."""
    with SmtpSink(username="mailer", password=PASSWORD, auth_fail_closes=True) as s:
        r = mailer.send_test(cfg(s.port, username="mailer", password="wrong-" + PASSWORD),
                             "user@example.com")
        assert r["status"] == "auth_failed", r
        assert r["message"].startswith("The server rejected the username or password.")
        assert "app password" in r["message"]
        assert r["detail"] == "535 5.7.8 Authentication credentials invalid"
        assert "Security" not in r["message"]
        assert PASSWORD not in repr(r)
        assert not s.messages
        # The right password still signs in with the same server behaviour.
        assert mailer.send_test(cfg(s.port, username="mailer", password=PASSWORD),
                                "user@example.com")["ok"]


def test_disconnect_during_sign_in_is_a_sign_in_failure() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        f.write(b"220 fake\r\n")
        f.flush()
        f.readline()  # EHLO
        f.write(b"250-fake\r\n250 AUTH LOGIN\r\n")
        f.flush()
        f.readline()  # AUTH LOGIN
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        r = mailer.send_test(cfg(srv.getsockname()[1], username="u", password="p4ssword"),
                             "user@example.com")
        assert r["status"] == "auth_failed", r
        assert "during sign-in" in r["message"]
    finally:
        srv.close()


def test_starttls_required_is_a_tls_hint_not_a_sign_in() -> None:
    """Gmail on 587 with Security None and no username: 530 5.7.0 Must issue a STARTTLS
    command first."""
    with SmtpSink(tls="starttls", require_tls=True) as s:
        r = mailer.send_test(cfg(s.port), "user@example.com")
        assert r["status"] == "tls_error", r
        assert "STARTTLS" in r["message"] and "sign-in" not in r["message"]
        assert r["detail"].startswith("530 5.7.0 Must issue a STARTTLS")


def test_non_ascii_recipient_without_smtputf8() -> None:
    with SmtpSink() as s:
        r = mailer.send_files(cfg(s.port), ["jos\u00e9@example.com"], [], "s", "b", [])
        assert r["status"] == "invalid", r
        assert "non-ASCII" in r["message"] and "SMTPUTF8" in r["message"]
        assert not s.messages


def test_starttls_on_an_ssl_port_fails_fast_with_a_hint(monkeypatch: pytest.MonkeyPatch
                                                        ) -> None:
    import time

    monkeypatch.setattr(mailer, "GREETING_TIMEOUT", 0.5)
    with SmtpSink(tls="ssl") as s:
        t0 = time.monotonic()
        r = mailer.send_test(cfg(s.port, security="starttls", timeout=20.0),
                             "user@example.com")
        assert time.monotonic() - t0 < 5
        assert r["status"] == "timeout", r
        assert "SSL/TLS" in r["message"] and "465" in r["message"]


def test_server_hanging_up_before_greeting_hints_at_security() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    t = threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True)
    t.start()
    try:
        r = mailer.send_test(cfg(srv.getsockname()[1]), "user@example.com")
        assert r["status"] == "tls_error", r
        assert "before greeting" in r["message"] and "SSL/TLS" in r["message"]
    finally:
        srv.close()


def test_scrub_short_passwords_only_as_whole_tokens() -> None:
    short = cfg(25, username="u", password="ab")
    assert mailer._scrub("table ab cab (ab)", short) == "table *** cab (***)"
    long = cfg(25, username="u", password="hunter22")
    assert mailer._scrub("xhunter22y", long) == "x***y"
    assert mailer._scrub("anything", cfg(25)) == "anything"


def test_from_params_port_and_display_name() -> None:
    base = {"host": "smtp.example.lab", "from_address": "a@example.com"}
    for bad in (0, "0", -1, 70000, "abc", 25.5, True):
        with pytest.raises(mailer.MailError) as e:
            mailer.SmtpConfig.from_params({**base, "port": bad})
        assert e.value.status == "invalid", bad
    assert mailer.SmtpConfig.from_params({**base, "port": ""}).port == 587
    assert mailer.SmtpConfig.from_params({**base, "port": "2525"}).port == 2525
    c = mailer.SmtpConfig.from_params({**base, "from_name": 'Ops\r\nBcc: x@example.com <"a">'})
    assert c.from_name == "Ops Bcc: x@example.com a"
    msg = mailer.build_message(c, ["a@example.com"], [], "s", "b")
    assert "\n" not in str(msg["From"])


def test_size_limit_under_one_mb_is_shown_in_kb(tmp_path: Path) -> None:
    assert mailer._fmt_mb(0.01) == "10.2 KB"
    assert mailer._fmt_mb(20) == "20 MB" and mailer._fmt_mb(2.5) == "2.5 MB"
    f = tmp_path / "r.pdf"
    f.write_bytes(b"\0" * 20_000)
    r = mailer.send_files(cfg(closed_port(), max_attachment_mb=0.01), ["user@example.com"],
                          [], "s", "b", [f])
    assert r["status"] == "too_large" and "over the 10.2 KB limit" in r["message"]


def test_send_notice_has_no_attachments(sink: SmtpSink) -> None:
    r = mailer.send_notice(cfg(sink.port), ["user@example.com"], ["cc@example.com"],
                           "Schedule failed:\nweekly", "Reason: down")
    assert r["ok"] and r["status"] == "ok", r
    msg = parse(sink.messages[-1]["data"])
    assert msg["Subject"] == "Schedule failed: weekly"
    assert not list(msg.iter_attachments())
    assert mailer.send_notice(cfg(sink.port), [], [], "s", "b")["status"] == "invalid"


# ─── EHLO name (no stall on a slow reverse lookup) ─────────────────────

@pytest.fixture
def ehlo_names(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The name each EHLO carried; the mailer's one-time FQDN lookup starts afresh."""
    import smtplib

    names: list[str] = []
    real = smtplib.SMTP.ehlo

    def ehlo(self: smtplib.SMTP, name: str = "") -> Any:
        names.append(name or self.local_hostname)
        return real(self, name)

    monkeypatch.setattr(smtplib.SMTP, "ehlo", ehlo)
    monkeypatch.setattr(mailer, "_ehlo_lookup", None)
    return names


def test_slow_fqdn_lookup_does_not_stall_a_send(monkeypatch: pytest.MonkeyPatch,
                                               ehlo_names: list[str]) -> None:
    """socket.getfqdn() blocked for 30 to 70 s on macOS CI runners, and smtplib calls it
    before connecting: the mailer waits EHLO_NAME_TIMEOUT at most, then names itself by
    its address."""
    import time

    release = threading.Event()
    monkeypatch.setattr(socket, "getfqdn", lambda *_a: release.wait(30) and "late.example.lab")
    monkeypatch.setattr(mailer, "EHLO_NAME_TIMEOUT", 0.2)
    try:
        t0 = time.monotonic()
        with SmtpSink() as s:
            assert mailer.send_test(cfg(s.port), "user@example.com")["ok"]
        with SmtpSink(tls="ssl") as s:
            r = mailer.send_test(cfg(s.port, security="ssl", cafile=str(demo_ca_file())),
                                 "user@example.com")
            assert r["ok"], r
        assert time.monotonic() - t0 < 10
        assert ehlo_names and set(ehlo_names) == {"[127.0.0.1]"}
    finally:
        release.set()


@pytest.mark.parametrize(("fqdn", "expected"), [("mailer.example.lab", "mailer.example.lab"),
                                                ("mailer", "[127.0.0.1]")])
def test_ehlo_name_is_the_fqdn_when_it_answers(monkeypatch: pytest.MonkeyPatch,
                                               ehlo_names: list[str], sink: SmtpSink,
                                               fqdn: str, expected: str) -> None:
    """As smtplib: a fully qualified name when the lookup answers, else the address."""
    monkeypatch.setattr(socket, "getfqdn", lambda *_a: fqdn)
    assert mailer.send_test(cfg(sink.port), "user@example.com")["ok"]
    assert ehlo_names and set(ehlo_names) == {expected}

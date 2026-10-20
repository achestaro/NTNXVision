"""Sidecar email methods (test_smtp, send_email) and the names scheduled runs use
(`unique_name`, per-target cluster scope in inventory_multi), on synthetic data and the
local test SMTP sink."""

from __future__ import annotations

import email
import email.policy
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.clusterlist import list_clusters
from ntnxvision_core.synthetic.smtpsink import SmtpSink, closed_port

PW = {"username": "admin", "password": "demo"}


def _run(data_dir: Path, name: str, **extra: Any) -> dict[str, Any]:
    return sc._m_inventory(None, {"host": "pc01.demo.local", **PW, "port": 9440,
                                  "insecure": False, "name": name, "formats": [],
                                  "export_dir": str(data_dir / "exports"), **extra})


@pytest.fixture
def sink(tmp_path: Path) -> Iterator[SmtpSink]:
    with SmtpSink(out_dir=tmp_path / "mail") as s:
        yield s


def smtp(port: int, **kw: Any) -> dict[str, Any]:
    return {"host": "127.0.0.1", "port": port, "security": "none", "username": "",
            "password": "", "from_address": "ntnxvision@example.com", "from_name": "NTNXVision",
            "allow_untrusted": False, "max_attachment_mb": 20, "zip_attachments": False, **kw}


def test_test_smtp(demo: Any, data_dir: Path, sink: SmtpSink) -> None:
    r = sc._m_test_smtp(None, {"smtp": smtp(sink.port), "to": "user@example.com"})
    assert r["ok"] and r["status"] == "ok"
    assert r["message"] == "Test email sent to user@example.com."
    msg = email.message_from_bytes(sink.messages[0]["data"], policy=email.policy.default)
    assert msg["Subject"] == "NTNXVision test email"
    assert "Settings > Email" in msg.get_body(("plain",)).get_content()  # type: ignore[union-attr]
    # Failures are results, not JSON-RPC errors.
    assert sc._m_test_smtp(None, {"smtp": smtp(closed_port()), "to": "user@example.com"}
                           )["status"] == "unreachable"
    assert sc._m_test_smtp(None, {"smtp": {"host": ""}, "to": "x"})["status"] == "not_configured"
    assert sc._m_test_smtp(None, {"smtp": smtp(sink.port), "to": "nope"})["status"] == "invalid"


def test_send_email_end_to_end(demo: Any, data_dir: Path, sink: SmtpSink) -> None:
    snap = _run(data_dir, "pc01 weekly")
    r = sc._m_send_email(None, {
        "smtp": smtp(sink.port), "to": ["user@example.com"], "cc": ["cc@example.com"],
        "subject": "Report {target} {date} {snapshot} {nope}",
        "body": "Hello\n{summary}\n", "context": {"target": "PC01"},
        "snapshots": [snap["snapshot_path"]], "formats": ["excel"],
        "export_dir": str(data_dir / "exports"), "file_prefix": "ACME"})
    assert r["ok"], r
    today = date.today().isoformat()
    assert r["subject"] == f"Report PC01 {today} pc01 weekly {{nope}}"
    assert [f["format"] for f in r["files"]] == ["excel"]
    report = Path(r["files"][0]["path"])
    assert report.is_file() and report.name == f"ACME-pc01 weekly-{today}.xlsx"
    assert r["attachments"] == [{"name": report.name, "size_bytes": report.stat().st_size}]
    assert r["recipients"] == ["user@example.com", "cc@example.com"]

    eml = Path(sink.out_dir or "") / "message-1.eml"
    msg = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
    assert [a.get_filename() for a in msg.iter_attachments()] == [report.name]
    body = msg.get_body(("plain",)).get_content()  # type: ignore[union-attr]
    assert "Summary: 4 clusters," in body and "VMs" in body
    # The send is recorded in the snapshot's export history like any export.
    detail = sc._m_get_snapshot(None, {"snapshot": snap["snapshot_path"]})
    assert any(e["path"] == str(report) for e in detail["exports"])


def test_send_email_keeps_reports_when_sending_fails(demo: Any, data_dir: Path) -> None:
    snap = _run(data_dir, "pc01 offline")
    r = sc._m_send_email(None, {
        "smtp": smtp(closed_port()), "to": ["user@example.com"], "subject": "s", "body": "b",
        "snapshots": [snap["snapshot_path"]], "formats": ["excel"],
        "export_dir": str(data_dir / "exports")})
    assert r["status"] == "unreachable" and not r["ok"]
    assert Path(r["files"][0]["path"]).is_file()


def test_send_email_validates_before_writing(demo: Any, data_dir: Path) -> None:
    snap = _run(data_dir, "pc01 v")
    base = {"smtp": smtp(closed_port()), "subject": "s", "body": "b",
            "snapshots": [snap["snapshot_path"]], "formats": ["excel"]}
    assert sc._m_send_email(None, {**base, "to": []})["status"] == "invalid"
    assert sc._m_send_email(None, {**base, "to": ["bad"]})["status"] == "invalid"
    assert sc._m_send_email(None, {**base, "to": ["user@example.com"], "formats": []}
                            )["status"] == "invalid"
    with pytest.raises(NutanixApiError, match="outside the snapshots directory"):
        sc._m_send_email(None, {**base, "to": ["user@example.com"],
                                "snapshots": ["/etc/passwd"]})


def test_unique_name_suffixes_instead_of_refusing(demo: Any, data_dir: Path) -> None:
    first = _run(data_dir, "Weekly pc01", unique_name=True)
    second = _run(data_dir, "Weekly pc01", unique_name=True)
    third = _run(data_dir, "weekly PC01", unique_name=True)
    assert [first["snapshot_name"], second["snapshot_name"], third["snapshot_name"]] == [
        "Weekly pc01", "Weekly pc01-2", "weekly PC01-3"]
    with pytest.raises(sc.InvalidName, match="already exists"):
        _run(data_dir, "Weekly pc01")
    with pytest.raises(sc.InvalidName):
        _run(data_dir, "  ", unique_name=True)


def test_multi_unique_names_and_per_target_scope(demo: Any, data_dir: Path) -> None:
    _run(data_dir, "Nightly (pc01)")
    prod = [c["uuid"] for c in list_clusters({"host": "pc01.demo.local", **PW})["clusters"]
            if "PROD" in c["name"]]
    params: dict[str, Any] = {
        "targets": [
            {"host": "pc01.demo.local", **PW, "name": "Nightly (pc01)", "clusters": prod},
            {"host": "pc03.demo.local", **PW, "name": "Nightly (pc03)"},
        ],
        "formats": [], "combine": True, "combined_name": "Nightly", "unique_name": True,
        "export_dir": str(data_dir / "exports")}
    result = sc._m_inventory_multi(None, params)
    first, other = result["targets"]
    assert first["snapshot_name"] == "Nightly (pc01)-2"
    assert first["summary"]["clusters"] == 2
    assert first["scope"]["label"].startswith("2 of 4 clusters")
    assert other["snapshot_name"] == "Nightly (pc03)"
    assert result["combined"]["snapshot_name"] == "Nightly"
    with pytest.raises(NutanixApiError, match="at least one cluster"):
        sc._m_inventory_multi(None, {"targets": [
            {"host": "pc01.demo.local", **PW, "name": "x1", "clusters": []}], "formats": []})


def test_test_smtp_untrusted_returns_the_certificate_then_pins(demo: Any) -> None:
    """R2-03: the first test with an untrusted certificate sends nothing and shows it."""
    with SmtpSink(tls="starttls", cert="self") as tls_sink:
        params = {"smtp": smtp(tls_sink.port, security="starttls", allow_untrusted=True),
                  "to": "user@example.com"}
        r = sc._m_test_smtp(None, params)
        assert r["status"] == "untrusted_cert" and not tls_sink.messages
        fp = r["certificate"]["fingerprint_sha256"]
        params["smtp"]["pinned_sha256"] = fp
        assert sc._m_test_smtp(None, params)["ok"]
        params["smtp"]["pinned_sha256"] = "00" * 32
        assert "changed" in sc._m_test_smtp(None, params)["message"]
        assert len(tls_sink.messages) == 1


# ─── QA round ──────────────────────────────────────────────────────────


def _attachment_names(sink: SmtpSink, n: int) -> list[str]:
    msg = email.message_from_bytes(sink.messages[n]["data"], policy=email.policy.default)
    return [str(a.get_filename()) for a in msg.iter_attachments()]  # type: ignore[attr-defined]


def test_resending_keeps_stable_names_and_reuses_the_saved_copy(
        demo: Any, data_dir: Path, sink: SmtpSink) -> None:
    exports = data_dir / "exports"
    snap = _run(data_dir, "pc01 resend")
    today = date.today().isoformat()
    params = {"smtp": smtp(sink.port), "to": ["user@example.com"], "subject": "s",
              "body": "b", "snapshots": [snap["snapshot_path"]],
              "formats": ["excel", "pdf"], "export_dir": str(exports)}
    # A manual export of the same snapshot already holds the plain names.
    sc._m_export(None, {"snapshot": snap["snapshot_path"], "formats": ["excel"],
                        "export_dir": str(exports)})
    first = sc._m_send_email(None, params)
    second = sc._m_send_email(None, params)
    assert first["ok"] and second["ok"], (first, second)
    stable = [f"pc01 resend-{today}.xlsx", f"pc01 resend-{today}.pdf"]
    # Every send attaches the same names, consistent across formats.
    assert _attachment_names(sink, 0) == stable == _attachment_names(sink, 1)
    assert [a["name"] for a in second["attachments"]] == stable
    # The second send reuses the first send's copies: no -3 files.
    assert [f["path"] for f in first["files"]] == [f["path"] for f in second["files"]]
    names = sorted(p.name for p in exports.iterdir())
    assert names == sorted([f"pc01 resend-{today}.xlsx", f"pc01 resend-{today}-2.xlsx",
                            f"pc01 resend-{today}.pdf"]), names
    # Other options are other content: a new copy.
    third = sc._m_send_email(None, {**params, "formats": ["pdf"],
                                    "sections": ["executive_summary"]})
    assert third["ok"] and third["files"][0]["path"] != first["files"][1]["path"]
    # No temp folder is left behind.
    assert all(Path(a).exists() for a in [f["path"] for f in third["files"]])


def test_target_placeholder_uses_the_saved_target_name(demo: Any, data_dir: Path,
                                                       sink: SmtpSink) -> None:
    snap = _run(data_dir, "pc01 named")
    base = {"smtp": smtp(sink.port), "to": ["user@example.com"], "subject": "R {target}",
            "body": "b", "snapshots": [snap["snapshot_path"]], "formats": ["excel"],
            "export_dir": str(data_dir / "exports")}
    r = sc._m_send_email(None, {**base, "context": {
        "target_names": {"PC01.demo.local": "Production PC"}}})
    assert r["subject"] == "R Production PC"
    # No saved target for the host: the host.
    r = sc._m_send_email(None, base)
    assert r["subject"] == "R pc01.demo.local"


def test_estimates_use_measured_export_sizes(demo: Any, data_dir: Path) -> None:
    snap = _run(data_dir, "pc01 sizes")
    before = sc._m_get_snapshot(None, {"snapshot": snap["snapshot_path"]})["export_estimates"]
    assert not before["excel"]["measured"]
    out = sc._m_export(None, {"snapshot": snap["snapshot_path"], "formats": ["excel", "pdf"],
                              "export_dir": str(data_dir / "exports")})
    real = {f: Path(p).stat().st_size for f, p in out["paths"].items()}
    after = sc._m_get_snapshot(None, {"snapshot": snap["snapshot_path"]})["export_estimates"]
    assert after["excel"] == {**after["excel"], "bytes": real["excel"], "measured": True}
    assert after["pdf"]["bytes"] == real["pdf"] and not after["word"]["measured"]
    # The model is within a factor of two of the real files (it was 2 to 4 times high).
    for fmt in ("excel", "pdf"):
        assert real[fmt] / 2 < before[fmt]["bytes"] < real[fmt] * 2, (fmt, before, real)


def test_send_notice_and_header_safe_from_name(demo: Any, sink: SmtpSink) -> None:
    r = sc._m_send_notice(None, {"smtp": smtp(sink.port, from_name="Ops\r\nBcc: x@example.com"),
                                 "to": ["user@example.com"], "subject": "Schedule failed",
                                 "body": "Reason"})
    assert r["ok"], r
    msg = email.message_from_bytes(sink.messages[-1]["data"], policy=email.policy.default)
    assert msg["From"] == "\"Ops Bcc: x@example.com\" <ntnxvision@example.com>"
    assert msg["Bcc"] is None
    assert sc._m_send_notice(None, {"smtp": {**smtp(sink.port), "port": 0}, "to": ["a@example.com"],
                                    "subject": "s"})["status"] == "invalid"

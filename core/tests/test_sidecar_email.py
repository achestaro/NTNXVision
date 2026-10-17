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

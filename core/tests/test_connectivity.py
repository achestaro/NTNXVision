"""test_connection outcome classification, against the local mock over real HTTPS."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.connectivity import test_connection as check


def _pc(host: str, password: str = "demo", insecure: bool = False, **kw: Any) -> dict[str, Any]:
    return check({"mode": "prism_central", "host": host, "port": 9440, "username": "admin",
                  "password": password, "insecure": insecure, **kw})


def test_ok_reports_version_counts_and_routing(demo: Any) -> None:
    r = _pc("pc01.demo.local")
    assert r["status"] == "ok" and r["ok"] is True
    assert r["version"] == "pc.2024.3" and r["version_supported"] is True
    assert r["clusters"] == 4 and r["hosts"] == 28
    # totalAvailableResults: 1,214 workloads + the PC VM (the 3 ESXi records v4 fails to
    # serialize still count — they exist, they just cannot be read).
    assert r["vms"] == 1215
    api = r["api"]
    assert api["v4"] > api["v3"] > 0 and api["not_deployed"] >= 1  # Files not deployed
    assert sum(api.values()) == 22
    assert r["supported_range"] == "pc.2024.1 to 7.5.1.6"
    assert "within supported range" in r["detail"]


def test_below_v4_ga_routes_everything_to_v3(demo: Any) -> None:
    r = _pc("pc03.demo.local")
    assert r["status"] == "ok" and r["api"]["v4"] == 0 and r["api"]["v3"] > 10


def test_version_outside_range_is_a_warning_not_a_failure(demo: Any) -> None:
    r = _pc("legacy-pc.demo.local")
    assert r["status"] == "version_unsupported" and r["ok"] is True
    assert r["version_supported"] is False and "outside" in r["detail"]


def test_auth_failed(demo: Any) -> None:
    r = _pc("pc01.demo.local", password="wrong")
    assert r["status"] == "auth_failed" and r["ok"] is False


def test_empty_password_is_auth_failed_without_a_request(demo: Any) -> None:
    assert _pc("pc01.demo.local", password="")["status"] == "auth_failed"


def test_self_signed_certificate_is_described(demo: Any) -> None:
    r = _pc("selfsigned.demo.local")
    assert r["status"] == "untrusted_cert" and r["ok"] is False
    cert = r["certificate"]
    assert cert["self_signed"] is True
    assert "CN=selfsigned.demo.local" in cert["subject"]
    assert cert["subject"] == cert["issuer"]
    assert len(cert["fingerprint_sha256"].split(":")) == 32
    assert cert["not_after"].endswith("Z")
    # Skipping verification (lab mode) asks to trust the certificate first, then gets
    # through once its fingerprint is pinned (see test_tls_pinning.py).
    assert _pc("selfsigned.demo.local", insecure=True)["status"] == "trust_required"
    pinned = _pc("selfsigned.demo.local", insecure=True,
                 pinned_sha256=cert["fingerprint_sha256"])
    assert pinned["status"] == "ok"


def test_unreachable(demo: Any) -> None:
    r = _pc("down.demo.local")
    assert r["status"] == "unreachable" and "DNS" in r["message"]


def test_unexpected_answer_is_error(demo: Any) -> None:
    # The NDB host answers HTTP, but not with a Prism Central API → HTTP 404 at connect.
    r = _pc("ndb.demo.local")
    assert r["status"] == "error" and r["ok"] is False


def test_prism_element(demo: Any) -> None:
    r = check({"mode": "prism_element", "host": "pe01.demo.local", "port": 9440,
               "username": "admin", "password": "x", "insecure": False})
    assert r["status"] == "ok" and r["name"] == "phx-dr-01"
    assert r["hosts"] == 6 and r["vms"] == 212 and "v2.0 API" in r["detail"]
    assert r["supported_range"] == "AOS 6.5 to 7.5"


def test_ndb(demo: Any) -> None:
    r = check({"mode": "ndb", "host": "ndb.demo.local", "username": "admin",
               "password": "x", "insecure": False})
    assert r["status"] == "ok" and "12 database servers" in r["detail"]


def test_missing_host_is_an_error_result() -> None:
    assert check({"mode": "prism_central", "host": "", "password": "x"})["status"] == "error"

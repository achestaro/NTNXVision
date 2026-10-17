"""TLS trust on first use (SEC-06) and no-redirect (SEC-13), against the demo mock."""

from __future__ import annotations

import io
from typing import Any

import pytest
import requests
from requests.adapters import BaseAdapter

from ntnxvision_core import sidecar
from ntnxvision_core.api import PrismConnection, PrismCredentials, transport
from ntnxvision_core.api.element_client import ElementConnection, ElementCredentials
from ntnxvision_core.api.errors import (
    CertificateChanged,
    CertificateNotTrusted,
    NutanixApiError,
    RedirectBlocked,
)
from ntnxvision_core.api.ndb_client import NdbConnection, NdbCredentials
from ntnxvision_core.api.vcenter_client import VcenterConnection, VcenterCredentials
from ntnxvision_core.connectivity import describe_certificate
from ntnxvision_core.connectivity import test_connection as check

SELF = "selfsigned.demo.local"
WRONG = ":".join(["AB"] * 32)


def _pc(host: str, **kw: Any) -> dict[str, Any]:
    return check({"mode": "prism_central", "host": host, "port": 9440, "username": "admin",
                  "password": "demo", **kw})


def _fingerprint(host: str = SELF) -> str:
    cert = describe_certificate(host, 9440)
    assert cert is not None
    return str(cert["fingerprint_sha256"])


def test_insecure_without_a_pin_asks_for_trust_and_sends_no_credentials(demo: Any) -> None:
    r = _pc(SELF, insecure=True)
    assert r["status"] == "trust_required" and r["ok"] is False
    assert r["certificate"]["fingerprint_sha256"] == _fingerprint()
    assert r["certificate"]["not_after"].endswith("Z")
    assert r["certificate"]["subject"] and r["certificate"]["issuer"]
    # The mock saw no request: even a wrong password is not judged yet.
    assert _pc(SELF, insecure=True, password="wrong")["status"] == "trust_required"


def test_a_pinned_certificate_connects(demo: Any) -> None:
    r = _pc(SELF, insecure=True, pinned_sha256=_fingerprint())
    assert r["status"] == "ok", r
    # Plain hex without colons and lower case is the same fingerprint.
    plain = _fingerprint().replace(":", "").lower()
    assert _pc(SELF, insecure=True, pinned_sha256=plain)["status"] == "ok"


def test_a_different_certificate_is_reported_as_changed(demo: Any) -> None:
    r = _pc(SELF, insecure=True, pinned_sha256=WRONG)
    assert r["status"] == "cert_changed" and r["ok"] is False
    assert r["certificate"]["fingerprint_sha256"] == _fingerprint()
    assert "changed" in r["message"]


def test_verification_on_ignores_a_pin(demo: Any) -> None:
    assert _pc("pc01.demo.local", pinned_sha256=WRONG)["status"] == "ok"
    assert _pc(SELF, pinned_sha256=_fingerprint())["status"] == "untrusted_cert"


def test_pinned_prism_element_ndb_and_vcenter_modes(demo: Any) -> None:
    for mode, host in (("prism_element", "pe01.demo.local"), ("ndb", "ndb.demo.local"),
                       ("vcenter", "vcenter.demo.local")):
        base = {"mode": mode, "host": host, "port": 9440 if mode == "prism_element" else 443,
                "username": "admin", "password": "demo", "insecure": True}
        assert check(base)["status"] == "trust_required", mode
        pin = describe_certificate(host, base["port"])
        assert pin is not None
        ok = check({**base, "pinned_sha256": pin["fingerprint_sha256"]})
        assert ok["status"] == "ok", (mode, ok)
        assert check({**base, "pinned_sha256": WRONG})["status"] == "cert_changed", mode


def test_connection_classes_pin_and_report_a_change(demo: Any) -> None:
    pin = transport.normalize_fingerprint(_fingerprint())
    creds = PrismCredentials(host=SELF, username="admin", password="demo", verify_ssl=False,
                             pinned_sha256=pin)
    with PrismConnection(creds, timeout=10) as conn:
        assert conn.pc_version
    bad = PrismCredentials(host=SELF, username="admin", password="demo", verify_ssl=False,
                           pinned_sha256="ab" * 32)
    with pytest.raises(CertificateChanged, match="changed"):
        PrismConnection(bad, timeout=10).connect()


def test_collection_with_verification_off_requires_a_pin(demo: Any) -> None:
    with pytest.raises(CertificateNotTrusted, match="Trust this certificate"):
        sidecar._m_inventory(1, {"host": SELF, "username": "admin", "password": "demo",
                                 "port": 9440, "insecure": True, "name": "x", "formats": []})
    with pytest.raises(CertificateNotTrusted):
        sidecar._ndb_from({"host": "ndb.demo.local", "username": "u", "password": "p",
                           "insecure": True}, False)
    with pytest.raises(CertificateNotTrusted):
        sidecar._vcenter_from({"host": "vc.demo.local", "username": "u", "password": "p",
                               "insecure": True}, False)


def test_collection_with_a_pin_runs_and_a_changed_certificate_fails(demo: Any, data_dir: Any
                                                                    ) -> None:
    params = {"host": SELF, "username": "admin", "password": "demo", "port": 9440,
              "insecure": True, "name": "pinned", "formats": [],
              "export_dir": str(data_dir / "exports"), "pinned_sha256": _fingerprint()}
    assert sidecar._m_inventory(1, dict(params))["snapshot_path"]
    with pytest.raises(CertificateChanged):
        sidecar._m_inventory(1, {**params, "name": "changed", "pinned_sha256": WRONG})


def test_ndb_has_its_own_insecure_flag() -> None:
    pin = "cd" * 32
    inherited = sidecar._ndb_from({"host": "ndb.example.lab", "username": "u", "password": "p",
                                   "pinned_sha256": pin}, True)
    assert inherited is not None and inherited.verify_ssl is False
    own = sidecar._ndb_from({"host": "ndb.example.lab", "username": "u", "password": "p",
                             "insecure": False}, True)
    assert own is not None and own.verify_ssl is True and own.pinned_sha256 is None


def test_fingerprint_helpers() -> None:
    assert transport.normalize_fingerprint(WRONG) == "ab" * 32
    assert transport.normalize_fingerprint("ab" * 32) == "ab" * 32
    assert transport.normalize_fingerprint("nonsense") is None
    assert transport.normalize_fingerprint(None) is None
    assert transport.format_fingerprint("ab" * 32) == WRONG
    assert transport.require_pin("h", False, None) is None
    assert transport.require_pin("h", True, WRONG) == "ab" * 32


# ─── redirects ─────────────────────────────────────────────────────────


class _Redirector(BaseAdapter):
    """Answers every request with a 302 to another host."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        self.calls.append(request.url or "")
        resp = requests.Response()
        resp.status_code = 302
        resp.headers["Location"] = "https://evil.example.net/steal"
        resp.raw = io.BytesIO(b"")
        resp.url = request.url or ""
        resp.request = request
        return resp

    def close(self) -> None:
        return None


def _redirecting() -> tuple[requests.Session, _Redirector]:
    session = transport.new_session()
    adapter = _Redirector()
    session.mount("https://", adapter)
    return session, adapter


def test_a_redirect_is_an_error_and_is_never_followed() -> None:
    session, adapter = _redirecting()
    with pytest.raises(RedirectBlocked) as err:
        session.get("https://pc.example.lab:9440/x", headers={"Authorization": "Basic abc"})
    assert ("redirected to evil.example.net; redirects are not followed for security"
            in str(err.value))
    assert adapter.calls == ["https://pc.example.lab:9440/x"]  # no second request


def test_every_client_refuses_a_redirect() -> None:
    clients: list[Any] = []
    s, _ = _redirecting()
    clients.append(PrismConnection(PrismCredentials("pc.example.lab", "u", "p"), session=s))
    s, _ = _redirecting()
    clients.append(ElementConnection(ElementCredentials("pe.example.lab", "u", "p"), session=s))
    s, _ = _redirecting()
    clients.append(NdbConnection(NdbCredentials("ndb.example.lab", "u", "p"), session=s))
    s, _ = _redirecting()
    clients.append(VcenterConnection(VcenterCredentials("vc.example.lab", "u", "p"), session=s))
    for client in clients:
        with pytest.raises(RedirectBlocked, match="redirects are not followed"):
            client.connect()
    # Closing a vCenter session never raises, even when the logout is redirected.
    clients[3].session.headers["vmware-api-session-id"] = "token"
    clients[3].close()


def test_the_connection_test_reports_a_redirect_as_an_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Route every new session through the redirecting adapter, as demo mode routes to its mock.
    monkeypatch.setattr(transport, "_session_hook",
                        lambda session: session.mount("https://", _Redirector()))
    monkeypatch.setattr(transport, "_address_hook", None)
    r = check({"mode": "prism_central", "host": "pc.example.lab", "port": 9440,
               "username": "u", "password": "p"})
    assert r["status"] == "error" and "redirected to evil.example.net" in r["message"]
    assert issubclass(RedirectBlocked, NutanixApiError)

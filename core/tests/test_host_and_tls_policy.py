"""R2-10 hostile hosts are refused everywhere, R2-11 no unpinned insecure TLS by default."""

from __future__ import annotations

import pathlib

import pytest
import requests
from typer.testing import CliRunner

from ntnxvision_core.__main__ import app
from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.element_client import ElementCredentials
from ntnxvision_core.api.errors import CertificateNotTrusted, InvalidHost
from ntnxvision_core.api.ndb_client import NdbCredentials
from ntnxvision_core.api.transport import GuardedSession, configure_tls
from ntnxvision_core.api.vcenter_client import VcenterCredentials
from ntnxvision_core.connectivity import describe_certificate
from ntnxvision_core.connectivity import test_connection as check
from ntnxvision_core.inventory import valid_hostname

HOSTILE = [
    "pc01.example.lab@198.51.100.7",
    "host?x",
    "host#x",
    "host/path",
    "https://host",
    "host:9440",
    "fe80::1%eth0",
    "[host]",
    "a b",
]
CLASSES = [PrismCredentials, ElementCredentials, NdbCredentials, VcenterCredentials]


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("host", [*HOSTILE, ""])
def test_credentials_refuse_hostile_hosts(cls: type, host: str) -> None:
    with pytest.raises(InvalidHost):
        cls(host=host, username="u", password="p")


@pytest.mark.parametrize("cls", CLASSES)
def test_credentials_build_safe_urls(cls: type) -> None:
    c6 = cls(host="2001:db8::7", username="u", password="p")
    assert "https://[2001:db8::7]" in c6.base
    c6b = cls(host="[2001:db8::7]", username="u", password="p")
    assert c6b.host == "2001:db8::7"
    c4 = cls(host=" 198.51.100.7 ", username="u", password="p")
    assert c4.host == "198.51.100.7"
    assert c4.base.startswith("https://198.51.100.7")
    assert "@" not in c4.base and "?" not in c4.base and "#" not in c4.base


def test_valid_hostname_is_shared_and_accepts_ipv6() -> None:
    assert valid_hostname("2001:db8::1") and valid_hostname("pc01.example.lab")
    assert not valid_hostname("pc01.example.lab@198.51.100.7")


@pytest.mark.parametrize("host", HOSTILE)
def test_connectivity_refuses_hostile_hosts_and_probe_never_dials(host: str) -> None:
    out = check({"mode": "prism_central", "host": host, "username": "u", "password": "p",
                 "insecure": True})
    assert out["status"] == "error" and not out["ok"]
    assert describe_certificate(host, 9440) is None


def test_configure_tls_needs_pin_or_explicit_allowance() -> None:
    s = GuardedSession()
    with pytest.raises(CertificateNotTrusted):
        configure_tls(s, False)
    with pytest.raises(CertificateNotTrusted):
        configure_tls(s, False, "not-a-fingerprint")
    configure_tls(s, False, allow_unpinned=True)
    assert s.verify is False
    configure_tls(requests.Session(), True)
    configure_tls(GuardedSession(), False, "ab" * 32)


def test_connection_insecure_without_pin_refused_before_any_request() -> None:
    creds = PrismCredentials(host="pc01.example.lab", username="u", password="p",
                             verify_ssl=False)
    with pytest.raises(CertificateNotTrusted):
        PrismConnection(creds).connect()


def test_cli_warns_and_accepts_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[PrismCredentials] = []

    def fake_collect(creds: PrismCredentials, **_kw: object) -> object:
        seen.append(creds)
        raise SystemExit(0)

    monkeypatch.setattr("ntnxvision_core.__main__.collect_environment", fake_collect)
    runner = CliRunner()
    args = ["inventory", "--host", "pc01.example.lab", "--username", "u", "--password", "p"]
    res = runner.invoke(app, [*args, "--insecure"])
    assert "WARNING" in res.output
    assert seen[-1].allow_unpinned is True and seen[-1].pinned_sha256 is None
    runner.invoke(app, [*args, "--insecure", "--pin", "ab" * 32])
    assert seen[-1].allow_unpinned is False and seen[-1].pinned_sha256 == "ab" * 32
    bad = runner.invoke(app, [*args, "--insecure", "--pin", "zz"])
    assert bad.exit_code != 0
    runner.invoke(app, args)
    assert seen[-1].allow_unpinned is False and seen[-1].verify_ssl is True


def test_only_the_cli_passes_allow_unpinned() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "ntnxvision_core"
    offenders = [str(p) for p in root.rglob("*.py")
                 if p.name not in ("__main__.py", "transport.py")
                 and "allow_unpinned=True" in p.read_text()]
    assert offenders == []

"""Connection tests for Prism Central, Prism Element and NDB — read-only, no collection.

Every outcome the user can act on is a *result*, not an error, classified as:

=======================  ====  ===========================================================
``ok``                   true  authenticated, version inside the supported range
``version_unsupported``  true  authenticated, version outside it (warn but proceed)
``auth_failed``          false 401 / 403, or no password at all
``untrusted_cert``       false TLS verification failed; the certificate is described
``unreachable``          false DNS failure, refused, timeout, no route
``error``                false anything else
=======================  ====  ===========================================================

For a Prism Central the test also reports cluster / host / VM counts and how the 21
collection areas would be routed (v4 / v3 / not deployed) — the same negotiation a real
run does, probed with one cheap ``$limit=1`` request per v4 namespace. Only GET and v3
``list`` calls are made.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import ssl
import tempfile
from datetime import datetime, timezone
from typing import Any

from ntnxvision_core.api import PrismConnection, PrismCredentials, transport
from ntnxvision_core.api.element_client import ElementConnection, ElementCredentials
from ntnxvision_core.api.errors import (
    AuthenticationError,
    CertificateChanged,
    ConnectionFailed,
    InvalidHost,
    NutanixApiError,
    TlsVerificationFailed,
)
from ntnxvision_core.api.ndb_client import NdbConnection, NdbCredentials
from ntnxvision_core.api.vcenter_client import VcenterConnection, VcenterCredentials
from ntnxvision_core.areas import AREAS, TYPICAL_SECONDS
from ntnxvision_core.compat import check_aos, check_prism_central
from ntnxvision_core.numfmt import fmt_count
from ntnxvision_core.runinfo import AOS_RANGE, PC_RANGE
from ntnxvision_core.versions import pc_version

TEST_TIMEOUT = 10

#: v3 kinds whose 404 means "product not deployed" (area id → kind).
_DEPLOYMENT_PROBES = {
    "files": "file_server",
    "objects": "objectstores",
    "flow_networking": "vpcs",
    "xplay": "action_rules",
}


def test_connection(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "prism_central")
    host = str(params.get("host") or "").strip()
    port = int(params.get("port") or (443 if mode in ("ndb", "vcenter") else 9440))
    username = str(params.get("username") or "")
    password = str(params.get("password") or "")
    insecure = bool(params.get("insecure"))
    pinned = transport.normalize_fingerprint(params.get("pinned_sha256"))
    base: dict[str, Any] = {
        "product": mode, "name": host,
        "supported_range": AOS_RANGE if mode == "prism_element" else
        (None if mode in ("ndb", "vcenter") else PC_RANGE),
        "typical_seconds": TYPICAL_SECONDS.get(mode, 60),
    }
    if not host:
        return {**base, "status": "error", "ok": False, "message": "Enter a host name or IP"}
    try:
        host = transport.clean_host(host)
    except InvalidHost as exc:
        return {**base, "status": "error", "ok": False, "message": str(exc)}
    base["name"] = host
    if not password:
        return {**base, "status": "auth_failed", "ok": False,
                "message": "No password. Type one, or save it to the keychain first"}
    if insecure and pinned is None:
        # Trust on first use: nothing is sent (least of all the password) to a server
        # whose certificate the user has not accepted. Describe it so they can decide.
        cert = describe_certificate(host, port)
        if cert is None:
            return {**base, "status": "unreachable", "ok": False,
                    "message": f"Could not reach {host}:{port} to read its certificate"}
        return {**base, "status": "trust_required", "ok": False,
                "message": "This server's certificate has not been trusted yet",
                "certificate": cert}
    try:
        if mode == "prism_element":
            return {**base, **_test_pe(host, port, username, password, insecure, pinned)}
        if mode == "ndb":
            return {**base, **_test_ndb(host, username, password, insecure, pinned)}
        if mode == "vcenter":
            return {**base, **_test_vcenter(host, port, username, password, insecure, pinned)}
        return {**base, **_test_pc(host, port, username, password, insecure, pinned)}
    except CertificateChanged:
        cert = describe_certificate(host, port)
        return {**base, "status": "cert_changed", "ok": False,
                "message": "The server certificate changed since you trusted it",
                "certificate": cert}
    except TlsVerificationFailed as exc:
        cert = describe_certificate(host, port)
        return {**base, "status": "untrusted_cert", "ok": False,
                "message": _tls_reason(exc, cert), "certificate": cert}
    except AuthenticationError:
        return {**base, "status": "auth_failed", "ok": False,
                "message": "HTTP 401 Unauthorized: the credentials were rejected"}
    except ConnectionFailed as exc:
        return {**base, "status": "unreachable", "ok": False, "message": _network_reason(exc)}
    except NutanixApiError as exc:
        return {**base, "status": "error", "ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the test must always answer
        return {**base, "status": "error", "ok": False,
                "message": f"{type(exc).__name__}: {exc}"}


# ─── Prism Central ─────────────────────────────────────────────────────


def _test_pc(host: str, port: int, username: str, password: str, insecure: bool,
             pinned: str | None = None) -> dict[str, Any]:
    from ntnxvision_core.api import v4_client

    creds = PrismCredentials(host=host, username=username, password=password, port=port,
                             verify_ssl=not insecure, pinned_sha256=pinned)
    with PrismConnection(creds, timeout=TEST_TIMEOUT) as conn:
        compat = check_prism_central(conn.pc_version)
        version = pc_version(conn.pc_version)
        clusters = _count_pc_clusters(conn)
        hosts = _v3_total(conn, "hosts", minus=1)
        vms = _vm_total(conn)
        routing = {"v4": 0, "v3": 0, "v2": 0, "not_deployed": 0}
        not_deployed = {aid for aid, kind in _DEPLOYMENT_PROBES.items()
                        if not _v3_exists(conn, kind)}
        for area in AREAS:
            if area.id in not_deployed:
                routing["not_deployed"] += 1
            elif area.v4_area and v4_client.negotiate(conn, area.v4_area) == v4_client.AVAILABLE:
                routing["v4"] += 1
            elif area.id in ("licenses", "lcm"):
                # v4-only areas: no legacy path, so they will be skipped on this PC.
                routing["not_deployed"] += 1
            else:
                routing["v3"] += 1
    status = "ok" if compat.in_range else "version_unsupported"
    parts = [f"{fmt_count(clusters)} cluster{'s' if clusters != 1 else ''}" if clusters is not None
             else None]
    api = [f"v4 for {routing['v4']} areas" if routing["v4"] else None,
           f"v3 for {routing['v3']}" + (" areas" if not routing["v4"] else "")
           if routing["v3"] else None,
           f"{routing['not_deployed']} not deployed" if routing["not_deployed"] else None]
    parts.append(", ".join(a for a in api if a))
    parts.append("within supported range" if compat.in_range else "outside supported range")
    return {
        "status": status, "ok": True,
        "message": f"Connected to Prism Central {version or '(version unknown)'}",
        "detail": " · ".join(p for p in parts if p),
        "version": version, "version_supported": compat.in_range,
        "clusters": clusters, "hosts": hosts, "vms": vms, "api": routing,
        "certificate": None,
    }


def _count_pc_clusters(conn: PrismConnection) -> int | None:
    try:
        page = conn.post_json(f"{conn.creds.base}/api/nutanix/v3/clusters/list",
                              {"kind": "cluster", "length": 500})
    except NutanixApiError:
        return None
    count = 0
    for entity in page.get("entities") or []:
        services = (((entity.get("status") or {}).get("resources") or {}).get("config")
                    or {}).get("service_list") or []
        if "PRISM_CENTRAL" not in services:
            count += 1
    return count


def _v3_total(conn: PrismConnection, kind: str, minus: int = 0) -> int | None:
    try:
        page = conn.post_json(f"{conn.creds.base}/api/nutanix/v3/{kind}/list",
                              {"kind": kind.rstrip("s"), "length": 1})
    except NutanixApiError:
        return None
    total = (page.get("metadata") or {}).get("total_matches")
    return max(0, int(total) - minus) if isinstance(total, int) else None


def _vm_total(conn: PrismConnection) -> int | None:
    """User VMs as v4 sees them (AHV + ESXi), else v3's VM intents."""
    from ntnxvision_core.api import v4_client

    if v4_client.negotiate(conn, "vms") == v4_client.AVAILABLE:
        total = 0
        for path in ("vmm/v4.0/ahv/config/vms", "vmm/v4.0/esxi/config/vms"):
            with contextlib.suppress(NutanixApiError):
                meta = conn.v4_get(path, params={"$limit": 1}).get("metadata") or {}
                total += int(meta.get("totalAvailableResults") or 0)
        return total
    return _v3_total(conn, "vms")


def _v3_exists(conn: PrismConnection, kind: str) -> bool:
    try:
        conn.post_json(f"{conn.creds.base}/api/nutanix/v3/{kind}/list", {"length": 1})
    except NutanixApiError as exc:
        return exc.status_code != 404
    return True


# ─── Prism Element ─────────────────────────────────────────────────────


def _test_pe(host: str, port: int, username: str, password: str, insecure: bool,
             pinned: str | None = None) -> dict[str, Any]:
    creds = ElementCredentials(host=host, username=username, password=password, port=port,
                               verify_ssl=not insecure, pinned_sha256=pinned)
    with ElementConnection(creds, timeout=TEST_TIMEOUT) as conn:
        info = conn.cluster_info
        name = info.get("name") or host
        version = conn.aos_version
        hosts = _v2_total(conn, "hosts")
        vms = _v2_total(conn, "vms")
    compat = check_aos(version)
    detail = " · ".join(p for p in (
        f"{fmt_count(hosts)} hosts" if hosts is not None else None,
        f"{fmt_count(vms)} VMs" if vms is not None else None,
        "v2.0 API",
        "within supported range" if compat.in_range else "outside supported range",
    ) if p)
    return {
        "status": "ok" if compat.in_range else "version_unsupported", "ok": True,
        "message": f"Connected to {name} · AOS {version or 'unknown'}",
        "detail": detail, "name": name, "version": f"AOS {version}" if version else None,
        "version_supported": compat.in_range, "clusters": 1, "hosts": hosts, "vms": vms,
        "api": {"v4": 0, "v3": 0, "v2": 5, "not_deployed": 0}, "certificate": None,
    }


def _v2_total(conn: ElementConnection, path: str) -> int | None:
    try:
        meta = conn.get(path, params={"count": 1}).get("metadata") or {}
    except NutanixApiError:
        return None
    total = meta.get("grand_total_entities", meta.get("total_entities"))
    return int(total) if isinstance(total, int) else None


# ─── NDB ───────────────────────────────────────────────────────────────


def _test_ndb(host: str, username: str, password: str, insecure: bool,
              pinned: str | None = None) -> dict[str, Any]:
    creds = NdbCredentials(host=host, username=username, password=password,
                           verify_ssl=not insecure, pinned_sha256=pinned)
    with NdbConnection(creds, timeout=TEST_TIMEOUT) as conn:
        version = None
        with contextlib.suppress(NutanixApiError):
            about = conn.get("about")
            if isinstance(about, dict):
                version = about.get("version")
        servers = _len_or_none(conn, "dbservers")
        databases = _len_or_none(conn, "databases")
    detail = " · ".join(p for p in (
        f"{fmt_count(servers)} database servers" if servers is not None else None,
        f"{fmt_count(databases)} databases" if databases is not None else None,
        "Era API",
    ) if p)
    return {"status": "ok", "ok": True,
            "message": f"Connected to NDB{' ' + version if version else ''}",
            "detail": detail, "version": version, "version_supported": True,
            "api": {"v4": 0, "v3": 0, "v2": 0, "not_deployed": 0}, "certificate": None}


def _test_vcenter(host: str, port: int, username: str, password: str, insecure: bool,
                  pinned: str | None = None) -> dict[str, Any]:
    """Log in to vCenter and count its hosts and VMs (GETs; the session is ended)."""
    creds = VcenterCredentials(host=host, username=username, password=password, port=port,
                               verify_ssl=not insecure, pinned_sha256=pinned)
    with VcenterConnection(creds, timeout=TEST_TIMEOUT) as vc:
        version = vc.version
        hosts = vms = None
        with contextlib.suppress(NutanixApiError):
            data = vc.get("vcenter/host")
            hosts = len(data) if isinstance(data, list) else None
        with contextlib.suppress(NutanixApiError):
            data = vc.get("vcenter/vm")
            vms = len(data) if isinstance(data, list) else None
    detail = " · ".join(p for p in (
        f"{fmt_count(hosts)} hosts" if hosts is not None else None,
        f"{fmt_count(vms)} VMs" if vms is not None else None,
        "vSphere REST API",
    ) if p)
    return {"status": "ok", "ok": True,
            "message": f"Connected to vCenter{' ' + version if version else ''}",
            "detail": detail, "version": version, "version_supported": True,
            "hosts": hosts, "vms": vms,
            "api": {"v4": 0, "v3": 0, "v2": 0, "not_deployed": 0}, "certificate": None}


def _len_or_none(conn: NdbConnection, path: str) -> int | None:
    try:
        data = conn.get(path)
    except NutanixApiError:
        return None
    return len(data) if isinstance(data, list) else None


# ─── certificates ──────────────────────────────────────────────────────


def describe_certificate(host: str, port: int) -> dict[str, Any] | None:
    """Fetch the server certificate with verification off, purely to describe it."""
    try:
        host = transport.clean_host(host)
    except InvalidHost:
        return None
    dial_host, dial_port, known = transport.resolve_address(host, port)
    if not known:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((dial_host, dial_port), timeout=4) as sock, \
                ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except OSError:
        return None
    if not der:
        return None
    fingerprint = hashlib.sha256(der).hexdigest().upper()
    out: dict[str, Any] = {
        "subject": "", "issuer": "", "self_signed": False,
        "fingerprint_sha256": ":".join(fingerprint[i:i + 2] for i in range(0, 64, 2)),
        "not_after": None,
    }
    decoded = _decode(der)
    if decoded:
        out["subject"] = _dn(decoded.get("subject"))
        out["issuer"] = _dn(decoded.get("issuer"))
        out["self_signed"] = decoded.get("subject") == decoded.get("issuer")
        not_after = decoded.get("notAfter")
        if not_after:
            with contextlib.suppress(ValueError):
                ts = ssl.cert_time_to_seconds(not_after)
                out["not_after"] = datetime.fromtimestamp(ts, timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
    return out


def _decode(der: bytes) -> dict[str, Any] | None:
    """Decode a DER certificate with the stdlib's own parser (no extra dependency)."""
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return None
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(ssl.DER_cert_to_PEM_cert(der))
        result = decoder(path)
        return result if isinstance(result, dict) else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _dn(rdns: Any) -> str:
    if not rdns:
        return ""
    parts = []
    short = {"commonName": "CN", "organizationName": "O", "organizationalUnitName": "OU",
             "countryName": "C", "localityName": "L", "stateOrProvinceName": "ST"}
    for rdn in rdns:
        for key, value in rdn:
            parts.append(f"{short.get(key, key)}={value}")
    return ", ".join(parts)


def _tls_reason(exc: Exception, cert: dict[str, Any] | None) -> str:
    text = str(exc)
    if "self-signed" in text or "self signed" in text or (cert and cert.get("self_signed")):
        return "certificate verify failed: self-signed certificate"
    if "hostname" in text.lower() or "match" in text.lower():
        return "certificate verify failed: the name does not match the certificate"
    return "certificate verify failed: unable to get local issuer certificate"


def _network_reason(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "resolve" in lowered or "nodename" in lowered or "name or service" in lowered:
        return "DNS lookup failed: the host name does not resolve"
    if "timed out" in lowered or "timeout" in lowered:
        return f"Connection timed out after {TEST_TIMEOUT} s"
    if "refused" in lowered:
        return "Connection refused: nothing is listening on that port"
    if "no route" in lowered or "unreachable" in lowered:
        return "No route to host"
    return text.split(" (")[0] if len(text) > 160 else text

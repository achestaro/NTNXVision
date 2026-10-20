"""Verified TLS for every client in the core: the operating system's trust store first.

Why this module exists: the frozen sidecar ships its own OpenSSL, whose built-in CA
location (``OPENSSLDIR``) points at the build machine's Python install (on macOS
``/Library/Frameworks/Python.framework/.../etc/openssl``). That folder does not exist on
a user's machine, so ``ssl.create_default_context()`` there trusts *nothing* and every
verified handshake fails with "unable to get local issuer certificate". Source runs never
noticed because a developer's Python finds the system bundle.

`verified_context()` is the one place a verifying `ssl.SSLContext` is built:

* **macOS / Windows**: a `truststore` context. The handshake is verified by the OS itself
  (Security framework / CryptoAPI), so public CAs *and* enterprise CAs installed on the
  machine (a customer's internal SMTP relay or a Prism Central with a corporate
  certificate) are trusted exactly as the OS trusts them. On Windows the certifi bundle is
  loaded as well; truststore consults it only when the OS chain fails (a server without
  automatic root updates). On macOS the OS store is authoritative, so nothing is added: a
  root the user distrusted in Keychain stays distrusted.
* **Elsewhere, or if truststore cannot load**: OpenSSL with the system default paths plus
  the certifi bundle (shipped inside the frozen sidecar).

`TrustStoreAdapter` puts that context under `requests` for ``verify=True``. Verification
off is untouched: pinned (SHA-256 fingerprint, trust on first use) and allow-untrusted
connections never go through this module.
"""

from __future__ import annotations

import ssl
import sys
from typing import Any

import requests
from requests.adapters import HTTPAdapter

try:  # pragma: no cover - import outcome depends on the platform
    import truststore
except Exception:  # noqa: BLE001 - ImportError, or the OS library failing to load
    truststore = None  # type: ignore[assignment]

#: Platforms where truststore verifies with the OS (elsewhere it would only wrap OpenSSL).
_OS_STORE_PLATFORMS = ("darwin", "win32")


def certifi_bundle() -> str | None:
    """Path of the certifi CA bundle, or None when it is missing (never raises)."""
    try:
        import certifi

        path = certifi.where()
    except Exception:  # noqa: BLE001
        return None
    try:
        with open(path, "rb") as fh:
            return path if fh.read(64) else None
    except OSError:
        return None


def uses_os_store() -> bool:
    """True when verified contexts are checked by the operating system's trust store."""
    return truststore is not None and sys.platform in _OS_STORE_PLATFORMS


def verified_context(cafile: str | None = None) -> ssl.SSLContext:
    """A client context that verifies the certificate chain and the host name.

    `cafile` adds extra trust anchors (tests and the demo SMTP sink use it).
    """
    ctx: ssl.SSLContext
    bundle = certifi_bundle()
    if uses_os_store():
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if sys.platform == "win32" and bundle:
            ctx.load_verify_locations(cafile=bundle)
    else:
        ctx = ssl.create_default_context()  # OpenSSL's default paths (may be empty)
        if bundle:
            ctx.load_verify_locations(cafile=bundle)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if cafile:
        ctx.load_verify_locations(cafile=cafile)
    return ctx


class TrustStoreAdapter(HTTPAdapter):
    """`requests` adapter whose ``verify=True`` connections use `verified_context()`.

    Each adapter (one per session) builds its context once. ``verify=False`` (pinned or
    unpinned) and ``verify="<bundle path>"`` keep requests' own behaviour.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._verified: ssl.SSLContext | None = None
        super().__init__(*args, **kwargs)

    def verified_ssl_context(self) -> ssl.SSLContext:
        if self._verified is None:
            self._verified = verified_context()
        return self._verified

    def build_connection_pool_key_attributes(
            self, request: requests.PreparedRequest, verify: Any, cert: Any = None,
    ) -> Any:
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert)
        if verify is True and host_params.get("scheme") == "https":
            pool_kwargs["ssl_context"] = self.verified_ssl_context()
        return host_params, pool_kwargs

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        if verify is True and url.lower().startswith("https"):
            # Not requests' default: it would point the pool at certifi's file (and fail
            # outright if that file were missing). The pool's ssl_context decides trust.
            super().cert_verify(conn, url, False, cert)
            conn.cert_reqs = "CERT_REQUIRED"
            return
        super().cert_verify(conn, url, verify, cert)


def _macos_anchor_count() -> int | None:
    """Roots in the macOS system trust store (Security framework), or None."""
    import ctypes

    try:
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sec.SecTrustCopyAnchorCertificates.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        sec.SecTrustCopyAnchorCertificates.restype = ctypes.c_int32
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        anchors = ctypes.c_void_p()
        if sec.SecTrustCopyAnchorCertificates(ctypes.byref(anchors)) != 0 or not anchors:
            return None
        try:
            return int(cf.CFArrayGetCount(anchors))
        finally:
            cf.CFRelease(anchors)
    except Exception:  # noqa: BLE001
        return None


def _os_anchor_count() -> int | None:
    if sys.platform == "darwin":
        return _macos_anchor_count()
    if sys.platform == "win32":
        try:
            return len(ssl.enum_certificates("ROOT"))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None
    return None


def trust_summary() -> dict[str, Any]:
    """What verified TLS trusts in this process, for the packaging self-check.

    ``ok`` is True when verification has a non-empty trust store: the OS store (with at
    least one root) on macOS / Windows, else a loadable certifi bundle. No network use.
    """
    bundle = certifi_bundle()
    certifi_roots = 0
    if bundle:
        probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        probe.load_verify_locations(cafile=bundle)
        certifi_roots = probe.cert_store_stats().get("x509_ca", 0)
    os_store = uses_os_store()
    os_roots = _os_anchor_count() if os_store else None
    try:
        verified_context()
        context_ok = True
    except Exception:  # noqa: BLE001
        context_ok = False
    if sys.platform in _OS_STORE_PLATFORMS:
        ok = context_ok and os_store and bool(os_roots)
    else:
        ok = context_ok and certifi_roots > 0
    return {
        "ok": ok,
        "backend": "os" if os_store else "openssl+certifi",
        "platform": sys.platform,
        "truststore": getattr(truststore, "__version__", None),
        "os_roots": os_roots,
        "certifi_bundle": bundle,
        "certifi_roots": certifi_roots,
        "openssl": ssl.OPENSSL_VERSION,
    }

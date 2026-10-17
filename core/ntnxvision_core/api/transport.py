"""HTTP session factory shared by the Prism Central, Prism Element and NDB clients.

Every connection class builds its `requests.Session` here, which gives one place to
change how requests leave the process. Normally that is nothing at all — a plain
session. In demo mode (see `ntnxvision_core.synthetic.demo`) a router is installed that
sends requests for the synthetic demo hostnames to the local mock Nutanix server, so the
*real* connection, collector and engine code runs end to end with no lab.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

from ntnxvision_core.api.errors import CertificateChanged, CertificateNotTrusted, RedirectBlocked

_REDIRECTS = (301, 302, 303, 307, 308)
_FP_RE = re.compile(r"^[0-9A-Fa-f]{2}(:?[0-9A-Fa-f]{2}){31}$")

#: Installed by demo mode: given a fresh session, mount whatever adapters it needs.
_session_hook: Callable[[requests.Session], None] | None = None
#: Installed by demo mode: map (host, port) to the address actually dialled.
_address_hook: Callable[[str, int], tuple[str, int, bool]] | None = None


def normalize_fingerprint(value: str | None) -> str | None:
    """A SHA-256 certificate fingerprint as 64 lower-case hex digits; None if not one."""
    text = (value or "").strip()
    if not _FP_RE.match(text):
        return None
    return text.replace(":", "").lower()


def format_fingerprint(value: str) -> str:
    """64 hex digits as the upper-case colon form shown to the user (AA:BB:...)."""
    digits = value.replace(":", "").upper()
    return ":".join(digits[i:i + 2] for i in range(0, len(digits), 2))


def require_pin(host: str, insecure: bool, pinned: str | None) -> str | None:
    """The fingerprint to pin for an endpoint whose TLS verification is off.

    Verification on needs none (None). Off without a usable fingerprint is refused before
    anything is sent: the user has to test the connection and trust the certificate first.
    """
    if not insecure:
        return None
    digest = normalize_fingerprint(pinned)
    if digest is None:
        raise CertificateNotTrusted(
            f"The certificate of {host} has not been trusted yet. Open Targets, test the "
            "connection and choose Trust this certificate.")
    return digest


class GuardedSession(requests.Session):
    """The one session type every API client uses.

    * Redirects are never followed (``allow_redirects`` is forced off) and a 3xx answer is
      an error: a redirect to another host would carry the Authorization or vCenter
      session header with it.
    * With a pinned certificate (`pin`) a different leaf certificate fails the handshake;
      the failure is reported as `CertificateChanged`, not as a generic network error.
    """

    pinned: str | None = None

    def request(self, method: str, url: str, *args: Any,  # type: ignore[override]
                **kwargs: Any
                ) -> requests.Response:
        kwargs["allow_redirects"] = False
        return super().request(method, url, *args, **kwargs)

    def send(self, request: requests.PreparedRequest,
             **kwargs: Any
             ) -> requests.Response:
        kwargs["allow_redirects"] = False
        host = urlsplit(request.url or "").hostname or "the server"
        try:
            resp = super().send(request, **kwargs)
        except requests.exceptions.SSLError as exc:
            if self.pinned and "ingerprint" in str(exc):
                raise CertificateChanged(
                    f"The certificate of {host} changed since you trusted it. Test the "
                    "connection again and trust the new certificate if you expect it."
                ) from exc
            raise
        if resp.status_code in _REDIRECTS and resp.headers.get("Location"):
            target = urlsplit(resp.headers["Location"]).hostname or host
            resp.close()
            raise RedirectBlocked(
                f"The server redirected to {target}; redirects are not followed for security",
                status_code=resp.status_code)
        return resp


def configure_tls(session: requests.Session, verify: bool, pinned: str | None = None) -> None:
    """Set certificate verification on a session; with it off, pin the server certificate.

    `pinned` is a SHA-256 fingerprint: urllib3 then requires the leaf certificate to match
    it on every new connection (this replaces chain verification, which is off). Applied
    to whatever adapter is mounted for https (and to proxied connections), so demo mode's
    router keeps working. Verification on ignores `pinned`.
    """
    session.verify = verify
    digest = None if verify else normalize_fingerprint(pinned)
    if isinstance(session, GuardedSession):
        session.pinned = digest
    if digest is None:
        return
    adapter = session.get_adapter("https://")
    poolmanager = getattr(adapter, "poolmanager", None)
    if poolmanager is not None:
        poolmanager.connection_pool_kw["assert_fingerprint"] = digest
    original = getattr(adapter, "proxy_manager_for", None)
    if original is not None:
        def proxy_manager_for(proxy: str, **kw: Any) -> Any:
            kw["assert_fingerprint"] = digest
            return original(proxy, **kw)
        adapter.proxy_manager_for = proxy_manager_for  # type: ignore[attr-defined]


def new_session() -> requests.Session:
    session: requests.Session = GuardedSession()
    if _session_hook is not None:
        _session_hook(session)
    return session


def resolve_address(host: str, port: int) -> tuple[str, int, bool]:
    """The (host, port) a raw TLS probe should dial, and whether the name is known.

    Used where the code opens a socket itself rather than going through a session —
    fetching a certificate to describe it. The flag is False for a demo hostname that
    deliberately does not resolve.
    """
    if _address_hook is not None:
        return _address_hook(host, port)
    return host, port, True


def install(
    session_hook: Callable[[requests.Session], None] | None,
    address_hook: Callable[[str, int], tuple[str, int, bool]] | None,
) -> None:
    global _session_hook, _address_hook
    _session_hook = session_hook
    _address_hook = address_hook

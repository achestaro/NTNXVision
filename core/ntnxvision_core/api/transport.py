"""HTTP session factory shared by the Prism Central, Prism Element and NDB clients.

Every connection class builds its `requests.Session` here, which gives one place to
change how requests leave the process. It is also where the **read-only guard** lives
(`check_request`): every request any client sends is checked against a short allowlist
of methods and paths before it leaves the process, so a collector that tried to write
would fail closed instead of changing a customer system. Normally that is nothing at all — a plain
session. In demo mode (see `ntnxvision_core.synthetic.demo`) a router is installed that
sends requests for the synthetic demo hostnames to the local mock Nutanix server, so the
*real* connection, collector and engine code runs end to end with no lab.
"""

from __future__ import annotations

import contextvars
import ipaddress
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote, urlsplit

import requests

from ntnxvision_core.api.errors import (
    CertificateChanged,
    CertificateNotTrusted,
    InvalidHost,
    NutanixApiError,
    ReadOnlyViolation,
    RedirectBlocked,
)

#: A bare DNS name (labels of letters, digits, hyphens) or an IPv4 address.
# Labels may contain "_" (not strict DNS, but common in lab and AD names); the target
# editor (app/src/shared/host.ts) accepts the same set.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*$"
)


def valid_hostname(host: str | None) -> bool:
    """True for a plain hostname, IPv4 or IPv6 address (no scheme, path, port or userinfo).

    The single host check: every credentials class, the certificate probe and the Prism
    deep links use it, so the host that is displayed and matched is the host that is dialled.
    """
    if not host:
        return False
    if _HOSTNAME_RE.fullmatch(host):
        return True
    if "%" in host:
        return False
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


def clean_host(host: str) -> str:
    """The validated host: surrounding space and IPv6 brackets removed.

    Raises `InvalidHost` for anything that is not a plain hostname or IP address (userinfo,
    ``?``, ``#``, ``/``, ports, schemes, IPv6 zone ids).
    """
    value = (host or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
        if ":" not in value:
            raise InvalidHost(f"Invalid host name: {host!r}")
    if not valid_hostname(value):
        raise InvalidHost(f"Invalid host name: {host!r}. Enter a plain host name or IP address")
    return value


def url_host(host: str) -> str:
    """The host as it goes into a URL authority: IPv6 literals are bracketed."""
    return f"[{host}]" if ":" in host else host


_REDIRECTS = (301, 302, 303, 307, 308)
_FP_RE = re.compile(r"^[0-9A-Fa-f]{2}(:?[0-9A-Fa-f]{2}){31}$")

#: Installed by demo mode: given a fresh session, mount whatever adapters it needs.
_session_hook: Callable[[requests.Session], None] | None = None
#: Installed by demo mode: map (host, port) to the address actually dialled.
_address_hook: Callable[[str, int], tuple[str, int, bool]] | None = None


#: Largest API response body accepted (after decompression). Real estates page their
#: lists, so a single response is far below this; the cap stops a hostile or broken
#: endpoint from exhausting memory.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class BoundedSession(requests.Session):
    """A session that refuses response bodies larger than `max_bytes`."""

    max_bytes: int = MAX_RESPONSE_BYTES

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        stream = kwargs.get("stream", False)
        kwargs["stream"] = True
        resp = super().send(request, **kwargs)
        if stream:
            return resp
        limit = self.max_bytes
        mb = limit // (1024 * 1024)

        def too_big() -> NutanixApiError:
            resp.close()
            return NutanixApiError(
                f"Response from {request.url} is larger than the {mb} MB limit; not read.",
                status_code=resp.status_code)

        declared = resp.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > limit:
            raise too_big()
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > limit:
                raise too_big()
        resp._content = bytes(body)
        return resp


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


# ─── read-only guard ───────────────────────────────────────────────────

#: Requests that use a non-GET method but change nothing. Matched on the URL path only,
#: on any host: the patterns are not tied to the host a client was built for.
#:
#: * Prism Central v3 lists (``POST .../<kind>/list``) and the groups query API are
#:   reads that Nutanix models as POST;
#: * the vSphere Automation API session login (POST) and logout (DELETE), which create
#:   and end only the tool's own API session. ``/api/session`` is a generic path: it
#:   matches on *any* host (Prism Central included), so a POST or DELETE there is never
#:   refused by this guard. That is acceptable because neither Nutanix API serves a
#:   ``/api/session`` endpoint that changes anything, but it is a path match, not a
#:   vCenter-only rule.
_READ_ONLY_REQUESTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"/api/nutanix/v3/[a-z_]+/list")),
    ("POST", re.compile(r"/api/nutanix/v3/groups")),
    ("POST", re.compile(r"/api/session")),
    ("DELETE", re.compile(r"/api/session")),
    ("POST", re.compile(r"/rest/com/vmware/cis/session")),
    ("DELETE", re.compile(r"/rest/com/vmware/cis/session")),
)

#: The one request NTNXVision may send that starts work on a Nutanix system: an LCM
#: inventory, in one of two forms. Either is allowed only inside
#: `lcm_inventory_permit()`, which only `ntnxvision_core.lcm_inventory` enters, and only
#: when the user switched the option on for that run.
#:
#: * v4 lifecycle (pc.2024.3 and later): the inventory action, matched on its path.
LCM_INVENTORY_REQUEST = (
    "POST", re.compile(r"/api/lifecycle/v4\.\d+/operations/\$actions/inventory"))

# ─── the legacy LCM interface (Genesis RPC, pc.2024.1 / pc.2024.2) ──────
#
# Below the v4 lifecycle API the Prism UI drives LCM through Genesis, a *generic* RPC
# endpoint: ``POST /PrismGateway/services/rest/v1/genesis`` with a JSON body
# ``{"value": "<JSON text>"}`` naming an object, a method and its arguments. A path
# allowlist would admit every Genesis RPC (including ones that change a cluster), so the
# guard decodes the body and admits only the exact calls below. Every detail of that
# shape lives here, in one place; `ntnxvision_core.lcm_inventory` builds its bodies with
# `genesis_body()` so what is sent is what is checked.
#
# Shape from general Nutanix knowledge (the Prism UI's LCM page and public scripts), not
# yet verified against a live pc.2024.1 / pc.2024.2. See docs/ARCHITECTURE.md.

GENESIS_PATH = "/PrismGateway/services/rest/v1/genesis"
GENESIS_OID = "LifeCycleManager"
GENESIS_METHOD = "lcm_framework_rpc"
#: The key that carries the RPC's arguments inside the value JSON.
GENESIS_KWARGS = ".kwargs"
GENESIS_CLASS = "LcmFramework"
#: Read-only LCM framework methods (no arguments): the LCM configuration (its catalogue
#: URL) and the root task of an LCM operation in progress, if any.
GENESIS_READ_METHODS = frozenset({"get_config", "is_lcm_operation_in_progress"})
#: The inventory: one argument, the catalogue URL LCM is configured with.
GENESIS_INVENTORY_METHOD = "perform_inventory"
#: The one query parameter a Genesis call may carry: Prism Central's proxy to a
#: registered cluster's Prism Element (a cluster uuid).
_PROXY_QUERY = re.compile(r"proxyClusterUuid=[0-9A-Fa-f-]{1,64}")


def genesis_body(method: str, args: list[str] | None = None) -> dict[str, str]:
    """The JSON body of an LCM framework Genesis RPC (see the shape above)."""
    kwargs: dict[str, Any] = {"method_class": GENESIS_CLASS, "method": method}
    if args is not None:
        kwargs["args"] = list(args)
    return {"value": json.dumps({".oid": GENESIS_OID, ".method": GENESIS_METHOD,
                                 GENESIS_KWARGS: kwargs})}


def genesis_rpc(body: bytes | str | None) -> str | None:
    """The LCM framework method a Genesis body calls, if it is exactly one we know.

    Returns a method of `GENESIS_READ_METHODS` (no ``args``, or an empty list) or
    `GENESIS_INVENTORY_METHOD` (``args`` exactly one http(s) URL), else None: anything
    that is not JSON, has another key at any level, another object, RPC, class or
    method, or other arguments.
    """
    if body is None:
        return None
    try:
        outer = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        if not isinstance(outer, dict) or set(outer) != {"value"} or \
                not isinstance(outer["value"], str):
            return None
        rpc = json.loads(outer["value"])
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(rpc, dict) or set(rpc) != {".oid", ".method", GENESIS_KWARGS}:
        return None
    if rpc[".oid"] != GENESIS_OID or rpc[".method"] != GENESIS_METHOD:
        return None
    kwargs = rpc[GENESIS_KWARGS]
    if not isinstance(kwargs, dict) or kwargs.get("method_class") != GENESIS_CLASS:
        return None
    method = kwargs.get("method")
    keys = set(kwargs)
    if method in GENESIS_READ_METHODS:
        if keys == {"method_class", "method"} or \
                (keys == {"method_class", "method", "args"} and kwargs["args"] == []):
            return str(method)
        return None
    if method == GENESIS_INVENTORY_METHOD and keys == {"method_class", "method", "args"}:
        args = kwargs["args"]
        if isinstance(args, list) and len(args) == 1 and isinstance(args[0], str) and \
                re.fullmatch(r"https?://[^\s\"']{1,500}", args[0]):
            return GENESIS_INVENTORY_METHOD
    return None


_permit: contextvars.ContextVar[bool] = contextvars.ContextVar("lcm_inventory_permit",
                                                               default=False)


@contextmanager
def lcm_inventory_permit() -> Iterator[None]:
    """Allow the LCM inventory request for the duration of the block, in this context.

    A context variable, not a global: the permit does not leak to other threads (the
    collectors running alongside the inventory), and it ends with the block.
    """
    token = _permit.set(True)
    try:
        yield
    finally:
        _permit.reset(token)


def check_request(method: str | None, url: str | None,
                  body: bytes | str | None = None) -> None:
    """Refuse a request that is not known to be read-only, before it is sent.

    GET and HEAD always pass. Anything else must match `_READ_ONLY_REQUESTS`, be a
    read-only LCM Genesis RPC (body checked, `genesis_rpc`), or be the LCM inventory
    (v4 path, or the Genesis ``perform_inventory`` body) inside `lcm_inventory_permit()`.
    Raises `ReadOnlyViolation`.
    """
    verb = (method or "GET").upper()
    if verb in ("GET", "HEAD"):
        return
    parts = urlsplit(url or "")
    path = unquote(parts.path)
    for allowed, pattern in _READ_ONLY_REQUESTS:
        if verb == allowed and pattern.fullmatch(path):
            return
    if verb == "POST" and path == GENESIS_PATH and \
            (not parts.query or _PROXY_QUERY.fullmatch(parts.query)):
        rpc = genesis_rpc(body)
        if rpc in GENESIS_READ_METHODS:
            return
        if rpc == GENESIS_INVENTORY_METHOD and _permit.get():
            return
        raise ReadOnlyViolation(
            f"Refused {verb} {path}: only the LCM status reads and, when asked for, the "
            "LCM inventory may be sent through Genesis.")
    if _permit.get() and verb == LCM_INVENTORY_REQUEST[0] \
            and LCM_INVENTORY_REQUEST[1].fullmatch(path):
        return
    raise ReadOnlyViolation(
        f"Refused {verb} {path}: NTNXVision only reads from the systems it inventories.")


class GuardedSession(BoundedSession):
    """The one session type every API client uses.

    * Every request passes the read-only guard (`check_request`) before it is sent.
    * Responses are size-capped (`BoundedSession`).
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
        check_request(request.method, request.url, request.body)
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


def configure_tls(session: requests.Session, verify: bool, pinned: str | None = None,
                  *, allow_unpinned: bool = False) -> None:
    """Set certificate verification on a session; with it off, pin the server certificate.

    `pinned` is a SHA-256 fingerprint: urllib3 then requires the leaf certificate to match
    it on every new connection (this replaces chain verification, which is off). Applied
    to whatever adapter is mounted for https (and to proxied connections), so demo mode's
    router keeps working. Verification on ignores `pinned`.

    With verification off and no usable pin this raises `CertificateNotTrusted` unless
    `allow_unpinned=True` is passed explicitly (only the command line does, for --insecure,
    after warning the user). The sidecar never passes it.
    """
    session.verify = verify
    digest = None if verify else normalize_fingerprint(pinned)
    if not verify and digest is None and not allow_unpinned:
        raise CertificateNotTrusted(
            "TLS verification is off and the server certificate is not pinned. Trust the "
            "certificate first (Targets, test the connection).")
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

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
#: registered cluster's Prism Element (a cluster uuid, canonical form only).
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{12}")
_PROXY_QUERY = re.compile(r"proxyClusterUuid=" + UUID_RE.pattern)
#: A catalogue URL: http(s) and printable ASCII only, without quotes or backslashes.
_CATALOGUE_URL = re.compile(r"https?://[\x21\x23-\x26\x28-\x5b\x5d-\x7e]{1,500}")


def genesis_body(method: str, args: list[str] | None = None) -> dict[str, str]:
    """The JSON body of an LCM framework Genesis RPC (see the shape above)."""
    kwargs: dict[str, Any] = {"method_class": GENESIS_CLASS, "method": method}
    if args is not None:
        kwargs["args"] = list(args)
    return {"value": json.dumps({".oid": GENESIS_OID, ".method": GENESIS_METHOD,
                                 GENESIS_KWARGS: kwargs})}


class _Malformed(ValueError):
    """A Genesis body (or reply) the guard does not accept."""


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object hook: a key given twice is refused (parsers disagree on which wins)."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _Malformed(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise _Malformed(f"non-standard JSON constant {name}")


def _strict_json(text: str) -> Any:
    """JSON with unique keys at every level and no NaN / Infinity."""
    return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_no_constant)


def _body_text(body: object) -> str | None:
    """The body as text: str as is, bytes as strict UTF-8; anything else (bytearray,
    memoryview, a generator or file of a streamed upload) is None."""
    if type(body) is str:
        return body
    if type(body) is bytes:
        try:
            return body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    return None


def _decode_genesis(body: object) -> dict[str, Any] | None:
    """The RPC object of a Genesis body ``{"value": "<JSON text>"}``, strictly parsed."""
    text = _body_text(body)
    if text is None:
        return None
    try:
        outer = _strict_json(text)
        if not isinstance(outer, dict) or set(outer) != {"value"} or \
                not isinstance(outer["value"], str):
            return None
        rpc = _strict_json(outer["value"])
    except ValueError:  # JSONDecodeError and _Malformed
        return None
    return rpc if isinstance(rpc, dict) else None


def genesis_rpc(body: object) -> str | None:
    """The LCM framework method a Genesis body calls, if it is exactly one we know.

    Returns a method of `GENESIS_READ_METHODS` (no ``args``, or an empty list) or
    `GENESIS_INVENTORY_METHOD` (``args`` exactly one http(s) URL), else None: anything
    that is not strict JSON in str / UTF-8 bytes, repeats a key, has another key at any
    level, another object, RPC, class or method, or other arguments.
    """
    return _parse_rpc(body)[0]


def _parse_rpc(body: object) -> tuple[str | None, str | None]:
    """(method, catalogue URL for the inventory) of an accepted Genesis body."""
    rpc = _decode_genesis(body)
    if rpc is None or set(rpc) != {".oid", ".method", GENESIS_KWARGS}:
        return None, None
    if rpc[".oid"] != GENESIS_OID or rpc[".method"] != GENESIS_METHOD:
        return None, None
    kwargs = rpc[GENESIS_KWARGS]
    if not isinstance(kwargs, dict) or kwargs.get("method_class") != GENESIS_CLASS:
        return None, None
    method = kwargs.get("method")
    keys = set(kwargs)
    if method in GENESIS_READ_METHODS:
        if keys == {"method_class", "method"} or \
                (keys == {"method_class", "method", "args"} and kwargs["args"] == []):
            return str(method), None
        return None, None
    if method == GENESIS_INVENTORY_METHOD and keys == {"method_class", "method", "args"}:
        args = kwargs["args"]
        if isinstance(args, list) and len(args) == 1 and isinstance(args[0], str) and \
                _CATALOGUE_URL.fullmatch(args[0]):
            return GENESIS_INVENTORY_METHOD, args[0]
    return None, None


_permit: contextvars.ContextVar[bool] = contextvars.ContextVar("lcm_inventory_permit",
                                                               default=False)
#: The catalogue URL each LCM reported in its ``get_config`` reply, in this context:
#: (host and port, proxy query) to URL. Recorded by `GuardedSession.send` from the reply
#: itself; the inventory RPC must name exactly that URL for the same LCM.
_catalogues: contextvars.ContextVar[dict[tuple[str, str], str] | None] = \
    contextvars.ContextVar("lcm_catalogues", default=None)


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


def _lcm_key(parts: Any) -> tuple[str, str]:
    return (parts.netloc.lower(), parts.query)


def _remember_catalogue(url: str | None, content: bytes | None) -> None:
    """Record the catalogue URL of a successful ``get_config`` reply for that LCM."""
    try:
        text = _body_text(content)
        outer = _strict_json(text) if text is not None else None
        value = outer.get("value") if isinstance(outer, dict) else None
        decoded = _strict_json(value) if isinstance(value, str) else None
        ret = decoded.get(".return") if isinstance(decoded, dict) else None
        catalogue = ret.get("url") if isinstance(ret, dict) else None
    except ValueError:
        return
    if not isinstance(catalogue, str) or not _CATALOGUE_URL.fullmatch(catalogue):
        return
    known = dict(_catalogues.get() or {})
    known[_lcm_key(urlsplit(url or ""))] = catalogue
    _catalogues.set(known)


def _is_genesis_url(url: str | None) -> bool:
    """The Genesis endpoint exactly: raw (undecoded) path, no params, fragment or bare
    ``?``, and no query other than ``proxyClusterUuid=<uuid>``."""
    raw = url or ""
    parts = urlsplit(raw)
    if parts.path != GENESIS_PATH or parts.fragment or "#" in raw:
        return False
    if not parts.query:
        return "?" not in raw
    return bool(_PROXY_QUERY.fullmatch(parts.query))


def check_request(method: str | None, url: str | None,
                  body: object = None) -> None:
    """Refuse a request that is not known to be read-only, before it is sent.

    GET and HEAD pass when they carry no body and are not addressed to Genesis.
    Anything else must match `_READ_ONLY_REQUESTS`, be a read-only LCM Genesis RPC
    (exact URL, body checked, `genesis_rpc`), or be the LCM inventory inside
    `lcm_inventory_permit()`: the v4 path, or the Genesis ``perform_inventory`` naming the
    catalogue URL that LCM's own ``get_config`` reply gave in this context.
    Raises `ReadOnlyViolation`.
    """
    verb = (method or "GET").upper()
    parts = urlsplit(url or "")
    path = unquote(parts.path)
    genesis_like = "genesis" in path.lower()
    if verb in ("GET", "HEAD"):
        if body in (None, b"", "") and not genesis_like:
            return
        raise ReadOnlyViolation(
            f"Refused {verb} {path}: a read carries no body and never addresses Genesis.")
    for allowed, pattern in _READ_ONLY_REQUESTS:
        if verb == allowed and pattern.fullmatch(path):
            return
    if verb == "POST" and _is_genesis_url(url):
        rpc, catalogue = _parse_rpc(body)
        if rpc in GENESIS_READ_METHODS:
            return
        if rpc == GENESIS_INVENTORY_METHOD and _permit.get() and catalogue is not None \
                and (_catalogues.get() or {}).get(_lcm_key(parts)) == catalogue:
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
        # Captured before sending: an adapter (demo mode's router) may rewrite the URL.
        url, body = request.url, request.body
        check_request(request.method, url, body)
        remember = request.method == "POST" and _is_genesis_url(url) and \
            genesis_rpc(body) == "get_config"
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
        if remember and resp.status_code == 200:
            _remember_catalogue(url, resp.content)
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

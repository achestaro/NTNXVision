"""Demo mode: run the real app against the synthetic estate.

`enable()` starts two loopback HTTPS mock servers (one with the demo-CA-signed
certificate, one self-signed) and installs a router into `api.transport`, so every
`requests.Session` the connection classes create sends requests for ``*.demo.local``
hosts to the mock instead of the network:

* the URL is rewritten to ``https://127.0.0.1:<port>`` and the original hostname travels
  in the ``X-NTNXVision-Demo-Host`` header, which is how one mock serves every demo PC;
* with TLS verification on, the demo CA is trusted for demo hosts — except
  ``selfsigned.demo.local``, which is verified against the system store and fails exactly
  like a lab PC with its factory certificate;
* hosts marked unreachable raise a DNS-style `ConnectionError` without dialling;
* proxies are bypassed for demo hosts (a corporate HTTPS proxy must not see them).

Anything that is not a demo hostname passes through untouched, so demo mode never
changes how a real Prism Central is reached. Nothing here listens on a routable
interface: the mock binds 127.0.0.1 only, and only while demo mode is on.

After a successful collection the sidecar calls `advance(host)`, moving that system one
"week" forward (new VMs, retired ones, an AOS upgrade …) so the next collection gives
Compare something real to show.
"""

from __future__ import annotations

import os
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from ntnxvision_core.api import transport
from ntnxvision_core.synthetic.mockapi import MockApi, Target
from ntnxvision_core.synthetic.profiles import DEMO_DOMAIN, demo_targets, make_resolver
from ntnxvision_core.synthetic.server import HOST_HEADER, MockNutanixServer, demo_ca

DEFAULT_LATENCY_MS = 40.0


class DemoState:
    def __init__(self, targets: dict[str, Target], latency_ms: float) -> None:
        self.targets = targets
        self.api = MockApi(make_resolver(targets), latency_ms=latency_ms)
        self.ca_server = MockNutanixServer(self.api, tls="ca")
        self.self_server = MockNutanixServer(self.api, tls="self")
        self.ca_path = str(demo_ca())

    def start(self) -> None:
        self.ca_server.start()
        self.self_server.start()

    def stop(self) -> None:
        self.ca_server.stop()
        self.self_server.stop()

    def target(self, host: str) -> Target | None:
        return self.targets.get(host.lower().strip("."))

    def address(self, host: str, port: int) -> tuple[str, int, bool]:
        t = self.target(host)
        if t is None:
            if host.lower().endswith(DEMO_DOMAIN):
                return host, port, False
            return host, port, True
        if not t.reachable:
            return host, port, False
        server = self.self_server if t.tls == "self" else self.ca_server
        return "127.0.0.1", server.port, True


_state: DemoState | None = None
_lock = threading.Lock()


class DemoRouterAdapter(HTTPAdapter):
    """Sends requests for demo hostnames to the local mock servers."""

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        state = _state
        parts = urlsplit(request.url or "")
        host = (parts.hostname or "").lower()
        if state is None or not (host.endswith(DEMO_DOMAIN) or state.target(host)):
            return super().send(request, **kwargs)
        target = state.target(host)
        if target is None or not target.reachable:
            raise requests.exceptions.ConnectionError(
                f"Failed to resolve '{host}' ([Errno 8] nodename nor servname provided, "
                "or not known)"
            )
        dial_host, dial_port, _ = state.address(host, parts.port or 443)
        request.url = urlunsplit(
            (parts.scheme, f"{dial_host}:{dial_port}", parts.path, parts.query, parts.fragment))
        request.headers[HOST_HEADER] = host
        if kwargs.get("verify") is True and target.tls != "self":
            kwargs["verify"] = state.ca_path
        kwargs["proxies"] = {}
        return super().send(request, **kwargs)


def _session_hook(session: requests.Session) -> None:
    session.mount("https://", DemoRouterAdapter())


def _address_hook(host: str, port: int) -> tuple[str, int, bool]:
    state = _state
    if state is None:
        return host, port, True
    return state.address(host, port)


def enable(latency_ms: float | None = None, targets: dict[str, Target] | None = None
           ) -> DemoState:
    """Start the mock servers and route demo hostnames to them. Idempotent."""
    global _state
    with _lock:
        if _state is not None:
            return _state
        if latency_ms is None:
            raw = os.environ.get("NTNXVISION_DEMO_LATENCY_MS", "")
            latency_ms = float(raw) if raw.strip() else DEFAULT_LATENCY_MS
        state = DemoState(targets or demo_targets(), latency_ms)
        state.start()
        _state = state
        transport.install(_session_hook, _address_hook)
        return state


def disable() -> None:
    global _state
    with _lock:
        if _state is None:
            return
        transport.install(None, None)
        _state.stop()
        _state = None


def enabled() -> bool:
    return _state is not None


def state() -> DemoState | None:
    return _state


def advance(host: str) -> bool:
    """Move a demo system one epoch forward (synthetic drift for Compare)."""
    st = _state
    target = st.target(host) if st else None
    if target is None or target.kind in ("ndb", "vcenter"):
        return False
    target.advance()
    return True


def reset() -> None:
    """Put every demo system back at epoch 0 (tests; a fresh demo session)."""
    st = _state
    if st is None:
        return
    for target in st.targets.values():
        with target._lock:
            target.epoch = 0
            target._cache = {}
    st.api.reset()


def hosts() -> list[dict[str, str]]:
    """The demo hostnames, for docs and the UI hint."""
    st = _state
    targets = st.targets if st else demo_targets()
    return [{"host": t.host, "kind": t.kind} for t in targets.values()]

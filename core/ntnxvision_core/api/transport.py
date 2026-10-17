"""HTTP session factory shared by the Prism Central, Prism Element and NDB clients.

Every connection class builds its `requests.Session` here, which gives one place to
change how requests leave the process. Normally that is nothing at all — a plain
session. In demo mode (see `ntnxvision_core.synthetic.demo`) a router is installed that
sends requests for the synthetic demo hostnames to the local mock Nutanix server, so the
*real* connection, collector and engine code runs end to end with no lab.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests

from ntnxvision_core.api.errors import NutanixApiError

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


def new_session() -> requests.Session:
    session = BoundedSession()
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

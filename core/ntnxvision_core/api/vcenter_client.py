"""VMware vCenter Server client (vSphere Automation REST API), read-only.

An optional enrichment source for ESXi clusters: what Nutanix cannot say about an ESXi
VM, vCenter can (the datastore of a disk outside a Nutanix container, the switch type of
a port group, guest IPs and OS as VMware Tools reports them). See `collectors.vcenter`.

Two API generations are spoken, picked at connect time:

* ``/api`` (vSphere 7.0 U2 and later): ``POST /api/session`` returns the session id as a
  bare JSON string; lists are bare arrays; filters are repeated query parameters
  (``?hosts=host-1&hosts=host-2``).
* ``/rest`` (6.5 to 7.0): ``POST /rest/com/vmware/cis/session``; every answer is wrapped
  in ``{"value": ...}``; filters are ``filter.hosts``.

Read-only: the only non-GET requests are the session login (POST) and logout (DELETE),
which create and end this client's own API session and change nothing in the inventory.
Credentials are held in memory for the run only.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import requests
import urllib3

from ntnxvision_core.api.errors import (
    AuthenticationError,
    ConnectionFailed,
    NutanixApiError,
    TlsVerificationFailed,
)
from ntnxvision_core.api.transport import new_session
from ntnxvision_core.cancel import CancelToken

DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 60
SESSION_HEADER = "vmware-api-session-id"
#: Retries of a request vCenter answers 429 / 503 "busy" for (not the guest endpoints,
#: whose 503 means "VMware Tools not running" and is final).
RETRIES = 3


class VcenterError(NutanixApiError):
    """A vCenter request failed (status code kept, like the Nutanix errors)."""


@dataclass
class VcenterCredentials:
    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    verify_ssl: bool = True

    @property
    def base(self) -> str:
        port = "" if self.port == 443 else f":{self.port}"
        return f"https://{self.host}{port}"


@dataclass
class VcenterConnection:
    creds: VcenterCredentials
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=new_session)
    cancel: CancelToken | None = None
    #: "api" (vSphere 7.0 U2+) or "rest" (older), decided by `connect`.
    flavor: str = "api"
    version: str | None = None
    #: Seconds per backoff step on 429 / 503 (tests set 0).
    backoff: float = 1.0

    # ─── lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        if not self.creds.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session.verify = self.creds.verify_ssl
        self.session.headers.update({"Accept": "application/json"})
        auth = (self.creds.username, self.creds.password)
        resp = self._login("/api/session", auth)
        if resp.status_code == 404:
            self.flavor = "rest"
            resp = self._login("/rest/com/vmware/cis/session", auth)
        if resp.status_code in (401, 403):
            raise AuthenticationError("vCenter rejected the supplied credentials.",
                                      status_code=resp.status_code)
        if resp.status_code >= 400:
            raise VcenterError(f"vCenter login failed: HTTP {resp.status_code}",
                               status_code=resp.status_code)
        try:
            token = resp.json()
        except ValueError as exc:
            raise VcenterError("vCenter login returned no session id") from exc
        if isinstance(token, dict):
            token = token.get("value")
        if not isinstance(token, str) or not token:
            raise VcenterError("vCenter login returned no session id")
        self.session.headers[SESSION_HEADER] = token
        self.version = self._version()

    def _login(self, path: str, auth: tuple[str, str]) -> requests.Response:
        if self.cancel is not None:
            self.cancel.check()
        try:
            return self.session.post(f"{self.creds.base}{path}", auth=auth,
                                     timeout=self.timeout)
        except requests.exceptions.SSLError as exc:
            raise TlsVerificationFailed(
                f"TLS verification failed for vCenter {self.creds.host}. ({exc})") from exc
        except requests.exceptions.RequestException as exc:
            raise ConnectionFailed(
                f"Could not reach vCenter at {self.creds.host} ({exc})") from exc

    def close(self) -> None:
        """End the API session (logout), then the HTTP session. Never raises."""
        if SESSION_HEADER in self.session.headers:
            path = "/api/session" if self.flavor == "api" else "/rest/com/vmware/cis/session"
            with contextlib.suppress(requests.exceptions.RequestException):
                self.session.delete(f"{self.creds.base}{path}", timeout=10)
        self.session.close()

    def __enter__(self) -> VcenterConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ─── reads ─────────────────────────────────────────────────────

    def get(self, path: str, params: dict[str, Any] | None = None, *,
            retry_busy: bool = True) -> Any:
        """GET ``/api/<path>`` (or ``/rest/<path>``), unwrapped to the payload.

        `params` values that are lists become filters (repeated parameters on /api,
        ``filter.<name>`` on /rest).
        """
        prefix = "/api/" if self.flavor == "api" else "/rest/"
        query: dict[str, Any] = {}
        for key, value in (params or {}).items():
            name = key if self.flavor == "api" or not isinstance(value, list) \
                else f"filter.{key}"
            query[name] = value
        url = f"{self.creds.base}{prefix}{path.lstrip('/')}"
        resp: requests.Response | None = None
        for attempt in range(RETRIES + 1):
            if self.cancel is not None:
                self.cancel.check()
            resp = self.session.get(url, params=query, timeout=self.timeout)
            busy = resp.status_code == 429 or (retry_busy and resp.status_code == 503)
            if not busy or attempt == RETRIES:
                break
            time.sleep(self.backoff * (attempt + 1))
        assert resp is not None
        if resp.status_code in (401, 403):
            raise AuthenticationError(f"vCenter refused {path}: HTTP {resp.status_code}",
                                      status_code=resp.status_code)
        if resp.status_code >= 400:
            raise VcenterError(f"HTTP {resp.status_code} from vCenter {path}",
                               status_code=resp.status_code)
        try:
            payload = resp.json()
        except ValueError:
            return None
        if self.flavor == "rest" and isinstance(payload, dict) and "value" in payload:
            return payload["value"]
        return payload

    def get_optional(self, path: str) -> Any:
        """A GET whose 404 / 503 means "not available for this object" (VMware Tools not
        running, for the guest endpoints): None instead of an error."""
        try:
            return self.get(path, retry_busy=False)
        except VcenterError as exc:
            if exc.status_code in (404, 503):
                return None
            raise

    def _version(self) -> str | None:
        try:
            info = self.get("appliance/system/version")
        except NutanixApiError:
            return None
        if isinstance(info, dict):
            return str(info.get("version") or "") or None
        return None

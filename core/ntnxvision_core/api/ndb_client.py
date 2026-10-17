"""NDB (Era) REST API client.

NDB / Nutanix Database Service has its own control plane, separate from Prism Central:
a different base URL, its own credentials, and a `/era/v0.9` (and newer `/v1`) REST API.
This client is optional — the NDB collector only runs when an NDB endpoint is provided.
"""

from __future__ import annotations

import base64
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
from ntnxvision_core.api.transport import configure_tls, new_session
from ntnxvision_core.cancel import CancelToken

DEFAULT_TIMEOUT = 60


@dataclass
class NdbCredentials:
    host: str
    username: str
    password: str
    verify_ssl: bool = True
    #: SHA-256 fingerprint of the trusted certificate (only used with verify_ssl off).
    pinned_sha256: str | None = None

    @property
    def base(self) -> str:
        return f"https://{self.host}/era/v0.9"


@dataclass
class NdbConnection:
    creds: NdbCredentials
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=new_session)
    cancel: CancelToken | None = None

    def connect(self) -> None:
        if not self.creds.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        token = base64.b64encode(
            f"{self.creds.username}:{self.creds.password}".encode()
        ).decode()
        self.session.headers.update(
            {"Authorization": f"Basic {token}", "Accept": "application/json"}
        )
        configure_tls(self.session, self.creds.verify_ssl, self.creds.pinned_sha256)
        try:
            resp = self.session.get(f"{self.creds.base}/clusters", timeout=self.timeout)
        except requests.exceptions.SSLError as exc:
            raise TlsVerificationFailed(
                f"TLS verification failed for NDB at {self.creds.host} ({exc})"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ConnectionFailed(f"Could not reach NDB at {self.creds.host} ({exc})") from exc
        if resp.status_code in (401, 403):
            raise AuthenticationError("NDB rejected the supplied credentials.")
        if resp.status_code >= 400:
            raise NutanixApiError(f"NDB returned HTTP {resp.status_code}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.cancel is not None:
            self.cancel.check()
        resp = self.session.get(
            f"{self.creds.base}/{path.lstrip('/')}", params=params, timeout=self.timeout
        )
        if resp.status_code in (401, 403):
            raise AuthenticationError("Not authorized for this NDB resource.")
        if resp.status_code >= 400:
            raise NutanixApiError(f"HTTP {resp.status_code} from NDB {path}")
        try:
            return resp.json()
        except ValueError:
            return []

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> NdbConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

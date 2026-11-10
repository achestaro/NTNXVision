"""Kubernetes API client for NKP clusters (read-only).

An NKP cluster's own API server (``https://<control plane endpoint>:6443``) answers the
detail Prism Central cannot see: nodes as Kubernetes knows them, namespaces, workloads,
storage, load balancers, and NKP's own resources (Cluster API, Kommander). Like NDB, it is
optional and has its own endpoint and credential.

The credential is a bearer token (a ServiceAccount token, ideally bound to a read-only
role): it can live in the OS keychain like every other secret. Client-certificate
kubeconfigs are not used, because Python's TLS stack only reads a client key from a file,
and NTNXVision never writes credentials to disk. The API server's certificate is signed by
the cluster's own CA, so it is trusted the same way as a self-signed Prism Central: by
pinning its fingerprint.

Every request is a GET, through the same read-only guard as the Nutanix clients.
"""

from __future__ import annotations

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
from ntnxvision_core.api.transport import clean_host, configure_tls, new_session, url_host
from ntnxvision_core.cancel import CancelToken

DEFAULT_TIMEOUT = 60
DEFAULT_PORT = 6443


@dataclass
class KubeCredentials:
    host: str
    token: str
    port: int = DEFAULT_PORT
    #: The cluster name shown in reports when the API cannot tell it (NKP names its
    #: Cluster API object after the cluster, so it is usually found).
    name: str | None = None
    verify_ssl: bool = True
    #: SHA-256 fingerprint of the trusted certificate (only used with verify_ssl off).
    pinned_sha256: str | None = None
    #: Command line only: connect with verification off and no pin (--insecure).
    allow_unpinned: bool = False

    def __post_init__(self) -> None:
        self.host = clean_host(self.host)

    @property
    def base(self) -> str:
        return f"https://{url_host(self.host)}:{self.port}"


class KubeNotFound(NutanixApiError):
    """The resource type is not served (a CRD that is not installed)."""


class KubeForbidden(AuthenticationError):
    """HTTP 403: the token is valid but its role may not read this resource."""


@dataclass
class KubeConnection:
    creds: KubeCredentials
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=new_session)
    cancel: CancelToken | None = None

    def connect(self) -> None:
        if not self.creds.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session.headers.update({"Authorization": f"Bearer {self.creds.token}",
                                     "Accept": "application/json"})
        configure_tls(self.session, self.creds.verify_ssl, self.creds.pinned_sha256,
                      allow_unpinned=self.creds.allow_unpinned)
        try:
            resp = self.session.get(f"{self.creds.base}/version", timeout=self.timeout)
        except requests.exceptions.SSLError as exc:
            raise TlsVerificationFailed(
                f"TLS verification failed for the Kubernetes API at {self.creds.host} ({exc})"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ConnectionFailed(
                f"Could not reach the Kubernetes API at {self.creds.host}:{self.creds.port} "
                f"({exc})") from exc
        if resp.status_code in (401, 403):
            raise AuthenticationError("The Kubernetes API rejected the token.")
        if resp.status_code >= 400:
            raise NutanixApiError(f"The Kubernetes API returned HTTP {resp.status_code}")
        # /version is public on many clusters: prove the token works on a real read.
        self.get("/api/v1/namespaces", params={"limit": 1})

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.cancel is not None:
            self.cancel.check()
        resp = self.session.get(f"{self.creds.base}{path}", params=params,
                                timeout=self.timeout)
        if resp.status_code == 401:
            raise AuthenticationError("The Kubernetes API rejected the token.")
        if resp.status_code == 403:
            raise KubeForbidden(f"The token may not read {path}.")
        if resp.status_code == 404:
            raise KubeNotFound(f"Not served: {path}", status_code=404)
        if resp.status_code >= 400:
            raise NutanixApiError(f"HTTP {resp.status_code} from Kubernetes {path}",
                                  status_code=resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return {}

    def list(self, path: str, page_size: int = 500) -> list[dict[str, Any]]:
        """Every item of a Kubernetes list, following ``continue`` tokens."""
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if token:
                params["continue"] = token
            page = self.get(path, params=params)
            items.extend(page.get("items") or [])
            token = (page.get("metadata") or {}).get("continue")
            if not token:
                return items

    def version(self) -> dict[str, Any]:
        """``/version`` (``gitVersion`` "v1.35.2"); empty when it cannot be read."""
        try:
            data = self.get("/version")
        except NutanixApiError:
            return {}
        return data if isinstance(data, dict) else {}

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> KubeConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

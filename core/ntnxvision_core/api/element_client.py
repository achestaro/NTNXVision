"""Prism Element (direct cluster) connection — v2.0 REST API.

When a cluster is not registered to a Prism Central, or a single-cluster site is being
documented, NTNXVision can connect directly to the cluster's Prism Element and inventory
it via the **v2.0 API** (`/PrismGateway/services/rest/v2.0/`). This is supported on
AOS 6.5 through 7.5.

The v2.0 API returns flat entity objects (no spec/status envelope) and uses
`{entities, metadata}` paging on list endpoints. This client mirrors `PrismConnection`
where practical so the engine can treat both modes uniformly.
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
from ntnxvision_core.api.transport import clean_host, configure_tls, new_session, url_host
from ntnxvision_core.cancel import CancelToken
from ntnxvision_core.versions import short_version

DEFAULT_PORT = 9440
DEFAULT_TIMEOUT = 60


@dataclass
class ElementCredentials:
    """Credentials + endpoint for a cluster's Prism Element."""

    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    verify_ssl: bool = True
    #: SHA-256 fingerprint of the trusted certificate (only used with verify_ssl off).
    pinned_sha256: str | None = None
    #: Command line only: connect with verification off and no pin (--insecure).
    allow_unpinned: bool = False

    def __post_init__(self) -> None:
        self.host = clean_host(self.host)

    @property
    def base(self) -> str:
        return f"https://{url_host(self.host)}:{self.port}/PrismGateway/services/rest/v2.0"


@dataclass
class ElementConnection:
    creds: ElementCredentials
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=new_session)
    aos_version: str | None = None
    #: The raw v2.0 cluster entity read at connect time (name, node count, …).
    cluster_info: dict[str, Any] = field(default_factory=dict)
    cancel: CancelToken | None = None

    # ─── lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        if not self.creds.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        token = base64.b64encode(
            f"{self.creds.username}:{self.creds.password}".encode()
        ).decode()
        self.session.headers.update(
            {"Authorization": f"Basic {token}", "Accept": "application/json"}
        )
        configure_tls(self.session, self.creds.verify_ssl, self.creds.pinned_sha256,
                      allow_unpinned=self.creds.allow_unpinned)

        try:
            resp = self.session.get(f"{self.creds.base}/cluster", timeout=self.timeout)
        except requests.exceptions.SSLError as exc:
            raise TlsVerificationFailed(
                f"TLS verification failed for {self.creds.host}. For a lab cluster with a "
                f"self-signed cert, disable SSL verification. ({exc})"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ConnectionFailed(
                f"Could not reach Prism Element at {self.creds.host}:{self.creds.port} ({exc})"
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthenticationError("Prism Element rejected the supplied credentials.")
        if resp.status_code >= 400:
            raise NutanixApiError(
                f"Unexpected response from Prism Element: HTTP {resp.status_code}"
            )

        try:
            cluster = resp.json()
            self.cluster_info = cluster if isinstance(cluster, dict) else {}
            self.aos_version = short_version(cluster.get("version") or cluster.get("full_version"))
        except ValueError:
            self.aos_version = None

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> ElementConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ─── request helpers ───────────────────────────────────────────

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.cancel is not None:
            self.cancel.check()
        resp = self.session.get(
            f"{self.creds.base}/{path.lstrip('/')}", params=params, timeout=self.timeout
        )
        return self._json_or_raise(resp)

    def v2_list(
        self,
        path: str,
        page_size: int = 250,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all entities from a v2.0 list endpoint, following offset paging.

        `extra_params` are merged into every page request (e.g. the VM endpoint's
        include_vm_disk_config / include_vm_nic_config flags).
        """
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {"count": page_size, "offset": offset, **(extra_params or {})}
            payload = self.get(path, params=params)
            entities = payload.get("entities", []) or []
            results.extend(entities)
            metadata = payload.get("metadata", {}) or {}
            total = metadata.get("grand_total_entities")
            if total is None:
                total = metadata.get("total_entities")
            offset += len(entities)
            if not entities or (total is not None and offset >= total):
                break
        return results

    def v1_list(self, path: str, page_size: int = 500) -> list[dict[str, Any]]:
        """Read-only GET of a legacy v1 list (``/PrismGateway/services/rest/v1/``).

        Only for data v2.0 does not expose (the Controller VMs). v1 pages by ``page`` /
        ``count`` and reports ``metadata.grandTotalEntities``.
        """
        base = self.creds.base.rsplit("/v2.0", 1)[0] + "/v1"
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            if self.cancel is not None:
                self.cancel.check()
            resp = self.session.get(f"{base}/{path.lstrip('/')}",
                                    params={"page": page, "count": page_size},
                                    timeout=self.timeout)
            payload = self._json_or_raise(resp)
            entities = payload.get("entities", []) or []
            results.extend(e for e in entities if isinstance(e, dict))
            total = (payload.get("metadata") or {}).get("grandTotalEntities")
            if not entities or total is None or len(results) >= int(total):
                break
            page += 1
        return results

    def _json_or_raise(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise AuthenticationError("Not authorized for this Prism Element resource.")
        if resp.status_code == 404:
            raise NutanixApiError(f"Not found: {resp.url}")
        if resp.status_code >= 400:
            raise NutanixApiError(f"HTTP {resp.status_code} from {resp.url}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            return {}

"""Prism Central connection facade.

Wraps a `requests.Session` configured for a Prism Central endpoint, performs API version
negotiation, and exposes helpers the collectors use:

    conn.v3_post("vms/list", {...})           # v3 REST (list APIs are POST /list)
    conn.v4_get("clustermgmt/v4.0/config/clusters")   # v4 REST (SDKs wrap these)
    conn.get_json(url)                          # raw

For Phase 1 we call the v4/v3 REST endpoints directly through this session. When the
official per-namespace v4 SDKs are added (optional `ntnx-v4` extra), the `v4_client`
module adapts them to this same facade so collectors don't change.
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
)

DEFAULT_PORT = 9440
DEFAULT_TIMEOUT = 60


@dataclass
class PrismCredentials:
    """Credentials + endpoint for a Prism Central (never persisted to disk here)."""

    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    verify_ssl: bool = True

    @property
    def base(self) -> str:
        return f"https://{self.host}:{self.port}"


@dataclass
class PrismConnection:
    creds: PrismCredentials
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=requests.Session)
    pc_version: str | None = None
    negotiated: dict[str, str] = field(default_factory=dict)

    # ─── lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the session, verify auth, and detect the PC version."""
        if not self.creds.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        token = base64.b64encode(
            f"{self.creds.username}:{self.creds.password}".encode()
        ).decode()
        self.session.headers.update(
            {
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        self.session.verify = self.creds.verify_ssl

        # A cheap authenticated call that exists on every PC: v3 clusters list.
        try:
            resp = self.session.post(
                f"{self.creds.base}/api/nutanix/v3/clusters/list",
                json={"kind": "cluster", "length": 1},
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as exc:
            raise ConnectionFailed(
                f"TLS verification failed for {self.creds.host}. "
                f"For a lab PC with a self-signed cert, disable SSL verification. ({exc})"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ConnectionFailed(
                f"Could not reach Prism Central at {self.creds.host}:{self.creds.port} ({exc})"
            ) from exc

        if resp.status_code in (401, 403):
            raise AuthenticationError("Prism Central rejected the supplied credentials.")
        if resp.status_code >= 400:
            raise NutanixApiError(
                f"Unexpected response from Prism Central: HTTP {resp.status_code}"
            )

        self.pc_version = self._detect_pc_version()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> PrismConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ─── raw request helpers ───────────────────────────────────────

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.session.get(url, params=params, timeout=self.timeout)
        return self._json_or_raise(resp)

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(url, json=body, timeout=self.timeout)
        return self._json_or_raise(resp)

    # ─── v3 REST (list endpoints are POST .../list, paginated) ─────

    def v3_list(self, kind_path: str, page_size: int = 250) -> list[dict[str, Any]]:
        """Fetch all entities from a v3 `.../list` endpoint, following offset paging."""
        url = f"{self.creds.base}/api/nutanix/v3/{kind_path}/list"
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = {"length": page_size, "offset": offset}
            page = self.post_json(url, body)
            entities = page.get("entities", [])
            results.extend(entities)
            total = page.get("metadata", {}).get("total_matches")
            offset += len(entities)
            if not entities or (total is not None and offset >= total):
                break
        return results

    def v3_groups(
        self, entity_type: str, attributes: list[str], page_size: int = 250
    ) -> list[dict[str, Any]]:
        """Query the Prism Central groups API for an entity type.

        Some entities (notably storage containers) are not exposed via a dedicated
        `.../list` endpoint on Prism Central and must be read through
        `POST /api/nutanix/v3/groups`. Returns a list of flat dicts (attribute name → value)
        per entity, so callers read simple keys rather than the raw grouped shape.
        """
        url = f"{self.creds.base}/api/nutanix/v3/groups"
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = {
                "entity_type": entity_type,
                "group_member_count": page_size,
                "group_member_offset": offset,
                "group_member_attributes": [{"attribute": a} for a in attributes],
            }
            page = self.post_json(url, body)
            groups = page.get("group_results", []) or []
            batch = 0
            for group in groups:
                for entity in group.get("entity_results", []) or []:
                    flat: dict[str, Any] = {"entity_id": entity.get("entity_id")}
                    for data in entity.get("data", []) or []:
                        values = data.get("values", []) or []
                        val = values[0].get("values", [None])[0] if values else None
                        flat[data.get("name")] = val
                    results.append(flat)
                    batch += 1
            total = page.get("total_entity_count") or page.get("filtered_entity_count")
            offset += batch
            if batch == 0 or (total is not None and offset >= total):
                break
        return results

    # ─── v4 REST (SDKs wrap these; collectors may call directly) ────

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a v4 endpoint, e.g. 'clustermgmt/v4.0/config/clusters'."""
        url = f"{self.creds.base}/api/{path.lstrip('/')}"
        return self.get_json(url, params=params)

    def v4_list(self, path: str, page_size: int = 100) -> list[dict[str, Any]]:
        """Fetch all pages from a v4 list endpoint (uses $page/$limit query params)."""
        results: list[dict[str, Any]] = []
        page = 0
        while True:
            params = {"$page": page, "$limit": page_size}
            payload = self.v4_get(path, params=params)
            data = payload.get("data") or []
            results.extend(data)
            meta = payload.get("metadata") or {}
            total = meta.get("totalAvailableResults")
            page += 1
            if not data or (total is not None and len(results) >= total):
                break
        return results

    # ─── internals ─────────────────────────────────────────────────

    def _detect_pc_version(self) -> str | None:
        """Best-effort PC version detection via the cluster's software map."""
        try:
            data = self.get_json(
                f"{self.creds.base}/PrismGateway/services/rest/v1/cluster"
            )
            return data.get("version") or data.get("fullVersion")
        except NutanixApiError:
            return None

    def _json_or_raise(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise AuthenticationError("Not authorized for this resource.")
        if resp.status_code == 404:
            raise NutanixApiError(f"Not found: {resp.url}")
        if resp.status_code >= 400:
            raise NutanixApiError(
                f"HTTP {resp.status_code} from {resp.url}: {resp.text[:300]}"
            )
        try:
            return resp.json()
        except ValueError:
            return {}

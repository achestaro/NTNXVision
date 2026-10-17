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

DEFAULT_PORT = 9440
DEFAULT_TIMEOUT = 60

#: Extra attempts for a request Prism Central answers with HTTP 429 (rate limited).
RATE_LIMIT_RETRIES = 5
#: Base backoff in seconds when the 429 carries no usable hint; grows per attempt.
RATE_LIMIT_BACKOFF = 1.0
#: Never wait longer than this for one retry, whatever the server suggests.
RATE_LIMIT_MAX_WAIT = 10.0


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
    session: requests.Session = field(default_factory=new_session)
    pc_version: str | None = None
    negotiated: dict[str, str] = field(default_factory=dict)
    #: Checked before every request, so a cancelled run stops between pages.
    cancel: CancelToken | None = None
    #: The run's cluster selection (`ntnxvision_core.scope.Scope`); None = every cluster.
    scope: Any = None
    #: Requests retried after an HTTP 429 so far (the engine reports them per area).
    throttled: int = 0
    #: Seconds per backoff step on a 429 without a usable hint (tests set 0).
    rate_limit_backoff: float = RATE_LIMIT_BACKOFF

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
            raise TlsVerificationFailed(
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
        return self._json_or_raise(self._send("GET", url, params=params))

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._json_or_raise(self._send("POST", url, json=body))

    def _send(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """One request, retried with backoff while Prism Central answers HTTP 429.

        Every v4 namespace is rate limited per Prism Central (the lab PC advertises
        ``X-Api-Ratelimit-Limit: 7`` per one-second window per API, 80 overall), so any
        collector that pages quickly, or a multi-PC run sharing the budget, can be told
        to slow down. Retrying here, in the one client every collector uses, means no
        collector fails or silently loses records over a transient 429; before this,
        only the alerts collector retried and Flow failed outright. The wait honours
        ``Retry-After`` / ``X-Api-Ratelimit-Refresh-Period-Seconds`` when present, else
        grows linearly. The cancel token is checked before every attempt.
        """
        resp: requests.Response | None = None
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            if self.cancel is not None:
                self.cancel.check()
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if resp.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                return resp
            self.throttled += 1
            time.sleep(self._rate_limit_wait(resp, attempt))
        assert resp is not None
        return resp

    def _rate_limit_wait(self, resp: requests.Response, attempt: int) -> float:
        if self.rate_limit_backoff <= 0:
            return 0.0
        hint: float | None = None
        for header in ("Retry-After", "X-Api-Ratelimit-Refresh-Period-Seconds"):
            raw = resp.headers.get(header) if resp.headers is not None else None
            try:
                hint = float(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                hint = None
            if hint is not None:
                break
        if hint is None:
            hint = self.rate_limit_backoff
        return max(0.0, min(RATE_LIMIT_MAX_WAIT, hint * (attempt + 1)))

    # ─── Prism Element APIs through the Prism Central proxy ────────

    def pe_get(self, cluster_uuid: str, path: str, params: dict[str, Any] | None = None,
               version: str = "v2.0") -> Any:
        """GET a Prism Element API of a registered cluster through Prism Central.

        Prism Central forwards ``/PrismGateway/services/rest/<version>/...`` to the
        cluster named by ``proxyClusterUuid`` with the PC session, so a PC account
        reaches cluster-local data it could not read on the PE directly (the lab's PC
        account gets 401 on PE itself). Read-only: GET only.
        """
        url = f"{self.creds.base}/PrismGateway/services/rest/{version}/{path.lstrip('/')}"
        return self.get_json(url, params={**(params or {}), "proxyClusterUuid": cluster_uuid})

    def pe_v2_list(self, cluster_uuid: str, path: str, page_size: int = 250,
                   extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Every entity of a Prism Element v2.0 list endpoint, through the PC proxy.

        v2.0 endpoints disagree on their page-size parameter (``/vms`` reads ``length``,
        others ``count``), so both are sent with ``offset``. A page that repeats the
        first one (an endpoint ignoring ``offset``) ends the walk.
        """
        results: list[dict[str, Any]] = []
        offset = 0
        first_ids: set[Any] = set()
        while True:
            params = {"offset": offset, "length": page_size, "count": page_size,
                      **(extra_params or {})}
            payload = self.pe_get(cluster_uuid, path, params=params)
            if not isinstance(payload, dict):
                break
            entities = [e for e in (payload.get("entities") or []) if isinstance(e, dict)]
            ids = {e.get("uuid") for e in entities}
            if offset and ids and ids == first_ids:
                break
            if not offset:
                first_ids = ids
            results.extend(entities)
            meta = payload.get("metadata") or {}
            total = meta.get("grand_total_entities")
            if total is None:
                total = meta.get("total_entities")
            offset += len(entities)
            if not entities or total is None or offset >= int(total):
                break
        return results

    # ─── v3 REST (list endpoints are POST .../list, paginated) ─────

    def v3_list(self, kind_path: str, page_size: int = 250, *,
                filter: str | None = None) -> list[dict[str, Any]]:  # noqa: A002
        """Fetch all entities from a v3 `.../list` endpoint, following offset paging.

        `filter` is passed as the v3 FIQL ``filter`` (e.g. ``cluster_name==a,cluster_name==b``).
        """
        url = f"{self.creds.base}/api/nutanix/v3/{kind_path}/list"
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            body: dict[str, Any] = {"length": page_size, "offset": offset}
            if filter:
                body["filter"] = filter
            page = self.post_json(url, body)
            entities = page.get("entities", [])
            results.extend(entities)
            total = page.get("metadata", {}).get("total_matches")
            offset += len(entities)
            if not entities or (total is not None and offset >= total):
                break
        return results

    def v3_groups(
        self,
        entity_type: str,
        attributes: list[str],
        page_size: int = 250,
        filter_criteria: str | None = None,
        multi_valued: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Query the Prism Central groups API for an entity type.

        Some entities (notably storage containers) are not exposed via a dedicated
        `.../list` endpoint on Prism Central and must be read through
        `POST /api/nutanix/v3/groups`. Returns a list of flat dicts (attribute name → value)
        per entity, so callers read simple keys rather than the raw grouped shape.

        An attribute can carry several values (a VM's IP addresses, a NIC's IPv4
        addresses); only the first is kept unless the attribute is named in
        `multi_valued`, whose values then arrive as a list.
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
            if filter_criteria:
                body["filter_criteria"] = filter_criteria
            page = self.post_json(url, body)
            groups = page.get("group_results", []) or []
            batch = 0
            for group in groups:
                for entity in group.get("entity_results", []) or []:
                    flat: dict[str, Any] = {"entity_id": entity.get("entity_id")}
                    for data in entity.get("data", []) or []:
                        values = data.get("values", []) or []
                        inner = (values[0].get("values") or []) if values else []
                        if data.get("name") in multi_valued:
                            flat[data.get("name")] = [v for v in inner if v not in (None, "")]
                            continue
                        # A null attribute can arrive as an empty inner list.
                        flat[data.get("name")] = inner[0] if inner else None
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

    def v4_list(self, path: str, page_size: int = 100, *,
                filter: str | None = None) -> list[dict[str, Any]]:  # noqa: A002
        """Fetch all pages from a v4 list endpoint (uses $page/$limit query params).

        `filter` is passed as the OData ``$filter`` (see `scope.v4_list_scoped`)."""
        results: list[dict[str, Any]] = []
        page = 0
        while True:
            params: dict[str, Any] = {"$page": page, "$limit": page_size}
            if filter:
                params["$filter"] = filter
            payload = self.v4_get(path, params=params)
            data = payload.get("data") or []
            results.extend(data)
            meta = payload.get("metadata") or {}
            total = meta.get("totalAvailableResults")
            page += 1
            if not data or (total is not None and len(results) >= total):
                break
        return results

    def v4_list_tolerant(
        self, path: str, page_size: int = 100, *, filter: str | None = None  # noqa: A002
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch all pages, isolating and skipping records the server cannot serialize.

        Returns `(items, skipped_count)`.

        Some v4 collections contain individual entities that make the endpoint answer
        HTTP 500: on the lab PC two of 57 ESXi VMs do, which fails any page that
        includes them and so aborts the whole list. Losing the page to two bad rows is
        the wrong trade, so a failed page is retried one record at a time: the readable
        entities are kept and only the genuinely broken ones are skipped. The caller is
        expected to report the count rather than hide it.

        Only a server error (5xx) counts a record as skipped, and the walk never reads
        past the collection's ``totalAvailableResults``. An earlier version salvaged a
        full page of 100 slots for a 57-VM collection and counted every rate-limited
        read as a skipped VM, slots past the end included: 136 phantom VMs on a lab run
        that collected one 57-VM cluster. Rate limiting is retried in `_send`; any other
        error that is not a 5xx propagates.

        The slow per-record path runs only for pages that actually fail, so a healthy
        collection costs exactly the same as `v4_list`.
        """
        results: list[dict[str, Any]] = []
        skipped = 0
        page = 0
        extra = {"$filter": filter} if filter else {}
        total: int | None = None
        while True:
            try:
                payload = self.v4_get(path, params={"$page": page, "$limit": page_size,
                                                    **extra})
            except NutanixApiError as exc:
                if not _server_error(exc):
                    raise
                if total is None:
                    total = self._v4_total(path, extra)
                good, bad, reached_end = self._salvage_page(path, page, page_size, extra,
                                                            total)
                results.extend(good)
                skipped += bad
                if reached_end:
                    break
                page += 1
                continue
            data = payload.get("data") or []
            results.extend(data)
            meta = payload.get("metadata") or {}
            if meta.get("totalAvailableResults") is not None:
                total = int(meta["totalAvailableResults"])
            page += 1
            if not data or (total is not None and len(results) + skipped >= total):
                break
        return results, skipped

    def _v4_total(self, path: str, extra: dict[str, Any]) -> int | None:
        """The collection's size, from a one-record read (None when that fails too)."""
        for offset in range(3):
            try:
                payload = self.v4_get(path, params={"$page": offset, "$limit": 1, **extra})
            except NutanixApiError as exc:
                if not _server_error(exc):
                    raise
                continue
            value = (payload.get("metadata") or {}).get("totalAvailableResults")
            return int(value) if value is not None else None
        return None

    def _salvage_page(
        self, path: str, page: int, page_size: int, extra: dict[str, Any] | None = None,
        total: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Re-read one failed page record by record.

        Returns `(readable, skipped, reached_end)`. Reads stop at `total` when it is
        known; otherwise an empty answer means the offset is past the end of the
        collection, which is how the walk stops when the final page is also the
        failing one. Only 5xx answers count as skipped records.
        """
        good: list[dict[str, Any]] = []
        skipped = 0
        base = page * page_size
        stop = base + page_size if total is None else min(base + page_size, total)
        for offset in range(base, stop):
            try:
                payload = self.v4_get(path, params={"$page": offset, "$limit": 1,
                                                    **(extra or {})})
            except NutanixApiError as exc:
                if not _server_error(exc):
                    raise
                skipped += 1
                continue
            data = payload.get("data") or []
            if not data:
                # Past the end of the collection: nothing further on this page.
                return good, skipped, True
            good.extend(data)
        return good, skipped, total is not None and stop >= total

    # ─── v4 capability negotiation ─────────────────────────────────

    def v4_ready(self, area: str) -> bool:
        """May `area` be collected over the v4 API on this Prism Central?

        Collectors gate their v4 path on this and keep their v3/v2 path for when it is
        False — v4 is GA only from pc.2024.3 while the supported floor is pc.2024.1, so
        both paths have to stay live until the legacy APIs are retired. The verdict is
        probed once and cached; see `v4_client` for why a version check alone is not
        enough.
        """
        from ntnxvision_core.api import v4_client

        return v4_client.negotiate(self, area) == v4_client.AVAILABLE

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
            raise AuthenticationError("Not authorized for this resource.",
                                      status_code=resp.status_code)
        if resp.status_code == 404:
            raise NutanixApiError(f"Not found: {resp.url}", status_code=404)
        if resp.status_code >= 400:
            raise NutanixApiError(
                f"HTTP {resp.status_code} from {resp.url}: {resp.text[:300]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError:
            return {}


def _server_error(exc: NutanixApiError) -> bool:
    """A 5xx: the record (or page) exists but the server could not serve it."""
    return exc.status_code is not None and exc.status_code >= 500
